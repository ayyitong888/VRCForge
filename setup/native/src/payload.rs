//! The compressed archive is appended to the setup executable, followed by its
//! length, extracted file bytes, and this format marker. No second manifest or
//! content hash is needed to locate our own embedded archive.
use serde::Serialize;
use std::{fs::File, io::{Read, Seek, SeekFrom}, path::Path};

pub const MAGIC: &[u8] = b"VRCFORGE_SETUP_PAYLOAD_V1";
pub const REMOTE_MAGIC: &[u8] = b"VRCFORGE_SETUP_REMOTE__V1";
pub const FOOTER_BYTES: u64 = 16 + MAGIC.len() as u64;

#[derive(Clone, Debug, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct PayloadInfo {
    pub required_bytes: u64,
    pub app_bytes: u64,
    pub payload_bytes: u64,
    pub executable_bytes: u64,
    pub download_url: Option<String>,
}

pub fn inspect_file(path: &Path) -> Result<PayloadInfo, String> {
    let mut file = File::open(path).map_err(|e| format!("Cannot read setup {}: {e}", path.display()))?;
    let total = file.metadata().map_err(|e| e.to_string())?.len();
    if total < FOOTER_BYTES {
        return Err("This setup does not contain an application payload.".into());
    }
    file.seek(SeekFrom::End(-(FOOTER_BYTES as i64))).map_err(|e| e.to_string())?;
    let mut footer = vec![0; FOOTER_BYTES as usize];
    file.read_exact(&mut footer).map_err(|e| format!("Cannot read the setup payload footer: {e}"))?;
    let remote = &footer[16..] == REMOTE_MAGIC;
    if &footer[16..] != MAGIC && !remote {
        return Err("This setup does not contain an application payload. Use the complete offline installer.".into());
    }
    let appended_bytes = u64::from_le_bytes(footer[0..8].try_into().unwrap());
    let app_bytes = u64::from_le_bytes(footer[8..16].try_into().unwrap());
    let executable_bytes = total.checked_sub(FOOTER_BYTES).and_then(|n| n.checked_sub(appended_bytes))
        .filter(|n| *n > 0).ok_or("The embedded payload length extends beyond this setup file.")?;
    let (payload_bytes, download_url) = if remote {
        file.seek(SeekFrom::Start(executable_bytes)).map_err(|e| e.to_string())?;
        let mut metadata = Vec::new();
        file.take(appended_bytes).read_to_end(&mut metadata).map_err(|e| e.to_string())?;
        #[derive(serde::Deserialize)]
        #[serde(rename_all = "camelCase")]
        struct RemotePayload {url: String, payload_bytes: u64}
        let remote: RemotePayload = serde_json::from_slice(&metadata).map_err(|e| format!("Cannot read web installer download information: {e}"))?;
        (remote.payload_bytes, Some(remote.url))
    } else {(appended_bytes, None)};
    Ok(PayloadInfo {required_bytes:app_bytes.saturating_add(executable_bytes), app_bytes, payload_bytes, executable_bytes, download_url})
}

pub fn inspect_payload() -> Result<PayloadInfo, String> {
    inspect_file(&std::env::current_exe().map_err(|e| e.to_string())?)
}
