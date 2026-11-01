use serde::{Deserialize, Serialize};

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct InstallRequest {
    pub install_dir: String,
    pub fresh: bool,
    pub desktop_shortcut: bool,
    pub start_menu: bool,
}

#[derive(Clone, Deserialize)]
#[serde(rename_all = "camelCase")]
pub struct UninstallRequest {
    pub keep_data: bool,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupResult {
    pub install_dir: String,
    pub data_dir: String,
    pub data_cleared: bool,
    pub log_path: String,
}

#[derive(Clone, Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SetupProgress {
    pub stage: String,
    pub percent: u8,
}
