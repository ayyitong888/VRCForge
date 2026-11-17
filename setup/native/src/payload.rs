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
    #[serde(skip)]
    pub(crate) signing_header: Option<SigningHeader>,
}

#[derive(Clone, Debug)]
pub(crate) struct SigningHeader {
    pub(crate) checksum: u64,
    pub(crate) directory: u64,
}

fn read_at<const N: usize>(file: &mut File, offset: u64) -> std::io::Result<[u8; N]> {
    let mut bytes = [0; N];
    file.seek(SeekFrom::Start(offset))?;
    file.read_exact(&mut bytes)?;
    Ok(bytes)
}

// The PE security directory uses a file offset. Unlike other directories, its
// table lives outside the image, after the payload and up to seven pad bytes.
fn certificate_table(file: &mut File, total: u64) -> std::io::Result<Option<(u64, SigningHeader)>> {
    if read_at::<2>(file, 0)? != *b"MZ" {
        return Ok(None);
    }
    let pe = u32::from_le_bytes(read_at(file, 0x3c)?) as u64;
    let optional = pe + 24;
    if read_at::<4>(file, pe)? != *b"PE\0\0" {
        return Err(std::io::Error::other("Invalid setup PE header"));
    }
    let directory_base = match u16::from_le_bytes(read_at(file, optional)?) {
        0x10b => 96,
        0x20b => 112,
        _ => return Err(std::io::Error::other("Invalid setup PE optional header")),
    };
    let optional_size = u16::from_le_bytes(read_at(file, pe + 20)?) as u64;
    let security_offset = directory_base + 4 * 8;
    if optional_size < security_offset + 8 ||
        u32::from_le_bytes(read_at(file, optional + directory_base - 4)?) <= 4 {
        return Ok(None);
    }
    let directory = optional + security_offset;
    let offset = u32::from_le_bytes(read_at(file, directory)?) as u64;
    let size = u32::from_le_bytes(read_at(file, directory + 4)?) as u64;
    if offset == 0 && size == 0 {
        return Ok(None);
    }
    if offset < optional + optional_size || offset % 8 != 0 || size < 8 || offset + size != total {
        return Err(std::io::Error::other("Invalid setup certificate table"));
    }
    Ok(Some((offset, SigningHeader { checksum: optional + 64, directory })))
}

pub fn inspect_file(path: &Path) -> Result<PayloadInfo, String> {
    let mut file = File::open(path).map_err(|e| format!("Cannot read setup {}: {e}", path.display()))?;
    let total = file.metadata().map_err(|e| e.to_string())?.len();
    let signed = certificate_table(&mut file, total).map_err(|e| e.to_string())?;
    let boundary = signed.as_ref().map_or(total, |(offset, _)| *offset);
    let padding_limit = if signed.is_some() { 7 } else { 0 };
    let (footer, payload_end) = (|| {
        for padding in 0..=padding_limit {
            let end = boundary.checked_sub(padding)?;
            let footer = read_at::<{ FOOTER_BYTES as usize }>(&mut file, end.checked_sub(FOOTER_BYTES)?).ok()?;
            if &footer[16..] == MAGIC || &footer[16..] == REMOTE_MAGIC {
                return Some((footer, end));
            }
            if footer[footer.len() - 1] != 0 { break; }
        }
        None
    })().ok_or("This setup does not contain an application payload. Use the complete offline installer.")?;
    let remote = &footer[16..] == REMOTE_MAGIC;
    let appended_bytes = u64::from_le_bytes(footer[0..8].try_into().unwrap());
    let app_bytes = u64::from_le_bytes(footer[8..16].try_into().unwrap());
    let executable_bytes = payload_end.checked_sub(FOOTER_BYTES).and_then(|n| n.checked_sub(appended_bytes))
        .filter(|n| *n > 0).ok_or("The embedded payload length extends beyond this setup file.")?;
    let signing_header = signed.map(|(_, header)| header);
    if signing_header.as_ref().is_some_and(|header| header.directory + 8 > executable_bytes) {
        return Err("The embedded payload overlaps the setup PE header.".into());
    }
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
    Ok(PayloadInfo {required_bytes:app_bytes.saturating_add(executable_bytes), app_bytes, payload_bytes, executable_bytes, download_url, signing_header})
}

pub fn inspect_payload() -> Result<PayloadInfo, String> {
    inspect_file(&std::env::current_exe().map_err(|e| e.to_string())?)
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::io::Write;

    #[test]
    fn reads_payload_before_certificate_table_and_alignment_padding() {
        const DIRECTORY: usize = 0x80 + 24 + 112 + 4 * 8;
        for signed in [false, true] {
            for remote in [false, true] {
                let mut image = vec![0; 512];
                image[..2].copy_from_slice(b"MZ");
                image[0x3c..0x40].copy_from_slice(&0x80_u32.to_le_bytes());
                image[0x80..0x84].copy_from_slice(b"PE\0\0");
                image[0x94..0x96].copy_from_slice(&240_u16.to_le_bytes());
                image[0x98..0x9a].copy_from_slice(&0x20b_u16.to_le_bytes());
                image[0x104..0x108].copy_from_slice(&16_u32.to_le_bytes());
                let body: &[u8] = if remote {
                    br#"{"url":"https://example.com/payload","payloadBytes":42,"appBytes":100}"#
                } else {
                    b"abc"
                };
                image.extend_from_slice(body);
                image.extend_from_slice(&(body.len() as u64).to_le_bytes());
                image.extend_from_slice(&100_u64.to_le_bytes());
                image.extend_from_slice(if remote { REMOTE_MAGIC } else { MAGIC });
                if signed {
                    let offset = image.len().next_multiple_of(8);
                    image.resize(offset + 8, 0);
                    image[DIRECTORY..DIRECTORY + 4].copy_from_slice(&(offset as u32).to_le_bytes());
                    image[DIRECTORY + 4..DIRECTORY + 8].copy_from_slice(&8_u32.to_le_bytes());
                }
                let mut file = tempfile::NamedTempFile::new().unwrap();
                file.write_all(&image).unwrap();
                let info = inspect_file(file.path()).unwrap();
                assert_eq!(info.executable_bytes, 512);
                assert_eq!(info.app_bytes, 100);
                assert_eq!(info.payload_bytes, if remote { 42 } else { 3 });
                assert_eq!(info.download_url.as_deref(), remote.then_some("https://example.com/payload"));
            }
        }
    }
}
