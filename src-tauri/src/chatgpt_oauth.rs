use crate::backend::{backend_json_request, blocking_backend_json_request};
use serde::{de::DeserializeOwned, Deserialize, Serialize};
use serde_json::{json, Value};
use tungstenite::client::IntoClientRequest;

const STATUS_PATH: &str = "/api/chatgpt/status";
const LOGIN_PATH: &str = "/api/chatgpt/login";
const CANCEL_PATH: &str = "/api/chatgpt/cancel";
const LOGOUT_PATH: &str = "/api/chatgpt/logout";
const MODELS_PATH: &str = "/api/chatgpt/models";
const ACTIVATE_PATH: &str = "/api/chatgpt/activate";
const AUTHORIZATION_URL_ERROR: &str = "ChatGPT returned an unsupported sign-in URL.";

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopChatGptOAuthRequest {
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopChatGptOAuthAccountRequest {
    account_id: String,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct DesktopChatGptOAuthActivationRequest {
    account_id: String,
    model: String,
    #[serde(default)]
    thinking_level: String,
    timeout_ms: Option<u64>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatGptOAuthAccount {
    id: String,
    label: String,
    email: String,
    connected: bool,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatGptOAuthStatus {
    #[serde(default)]
    accounts: Vec<ChatGptOAuthAccount>,
    #[serde(default)]
    pending: bool,
    #[serde(default)]
    error: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    active_account_id: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    active_model: Option<String>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    active_thinking_level: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatGptOAuthModel {
    id: String,
    name: String,
    #[serde(default)]
    supported_reasoning_levels: Vec<ChatGptOAuthReasoningLevel>,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    default_reasoning_level: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatGptOAuthReasoningLevel {
    effort: String,
    #[serde(default, skip_serializing_if = "Option::is_none")]
    description: Option<String>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatGptOAuthModels {
    models: Vec<ChatGptOAuthModel>,
}

#[derive(Deserialize, Serialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatGptOAuthLogoutResult {
    revoked: bool,
}

#[derive(Deserialize)]
#[serde(rename_all = "camelCase")]
struct ChatGptOAuthLoginResult {
    authorization_url: String,
}

fn parse_response<T: DeserializeOwned>(value: Value) -> Result<T, String> {
    serde_json::from_value(value).map_err(|_| "ChatGPT returned an invalid response.".to_string())
}

fn validate_authorization_url(raw: &str) -> Result<String, String> {
    let request = raw
        .to_owned()
        .into_client_request()
        .map_err(|_| AUTHORIZATION_URL_ERROR.to_string())?;
    let uri = request.uri();
    let authority = uri.authority().map(|value| value.as_str());
    if uri.scheme_str() != Some("https")
        || !matches!(authority, Some("auth.openai.com" | "auth.openai.com:443"))
        || uri.path() != "/api/accounts/authorize"
    {
        return Err(AUTHORIZATION_URL_ERROR.to_string());
    }
    Ok(raw.to_string())
}

#[tauri::command]
pub async fn chatgpt_oauth_status(
    request: DesktopChatGptOAuthRequest,
) -> Result<ChatGptOAuthStatus, String> {
    let value = blocking_backend_json_request(move || {
        let value = backend_json_request("GET", STATUS_PATH.to_string(), None, request.timeout_ms)?;
        Ok(value)
    })
    .await?;
    parse_response(value)
}

#[tauri::command]
pub async fn chatgpt_oauth_login(request: DesktopChatGptOAuthAccountRequest) -> Result<(), String> {
    let value = blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            LOGIN_PATH.to_string(),
            Some(json!({ "account_id": request.account_id })),
            request.timeout_ms,
        )
    })
    .await?;
    let response: ChatGptOAuthLoginResult = parse_response(value)?;
    let authorization_url = validate_authorization_url(&response.authorization_url)?;

    // This URL comes from the app-session-authenticated route and is pinned to the official
    // authorization host/path before being handed to the user-owned system browser.
    crate::commands::open_validated_external_url(&authorization_url, "ChatGPT sign-in")
}

#[tauri::command]
pub async fn chatgpt_oauth_cancel(request: DesktopChatGptOAuthRequest) -> Result<(), String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            CANCEL_PATH.to_string(),
            Some(json!({})),
            request.timeout_ms,
        )
    })
    .await?;
    Ok(())
}

#[tauri::command]
pub async fn chatgpt_oauth_logout(
    request: DesktopChatGptOAuthAccountRequest,
) -> Result<ChatGptOAuthLogoutResult, String> {
    let value = blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            LOGOUT_PATH.to_string(),
            Some(json!({ "account_id": request.account_id })),
            request.timeout_ms,
        )
    })
    .await?;
    parse_response(value)
}

#[tauri::command]
pub async fn chatgpt_oauth_models(
    request: DesktopChatGptOAuthAccountRequest,
) -> Result<ChatGptOAuthModels, String> {
    let value = blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            MODELS_PATH.to_string(),
            Some(json!({ "account_id": request.account_id })),
            request.timeout_ms,
        )
    })
    .await?;
    parse_response(value)
}

#[tauri::command]
pub async fn chatgpt_oauth_activate(
    request: DesktopChatGptOAuthActivationRequest,
) -> Result<(), String> {
    blocking_backend_json_request(move || {
        backend_json_request(
            "POST",
            ACTIVATE_PATH.to_string(),
            Some(json!({ "account_id": request.account_id, "model": request.model, "thinking_level": request.thinking_level })),
            request.timeout_ms,
        )
    })
    .await?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::{
        validate_authorization_url, ChatGptOAuthModels, ChatGptOAuthStatus,
        DesktopChatGptOAuthActivationRequest,
    };

    #[test]
    fn activation_request_preserves_reasoning_level_for_backend_proxy() {
        let request: DesktopChatGptOAuthActivationRequest =
            serde_json::from_value(serde_json::json!({
                "accountId": "offline-account",
                "model": "gpt-5.6-sol",
                "thinkingLevel": "high",
            }))
            .expect("activation request should deserialize");
        assert_eq!(request.thinking_level, "high");
    }

    #[test]
    fn status_preserves_persisted_active_account_and_model() {
        let status: ChatGptOAuthStatus = serde_json::from_str(
            r#"{"accounts":[],"pending":false,"error":"","activeAccountId":"account-1","activeModel":"gpt-test","activeThinkingLevel":"low"}"#,
        )
        .expect("backend status should deserialize");

        assert_eq!(status.active_account_id.as_deref(), Some("account-1"));
        assert_eq!(status.active_model.as_deref(), Some("gpt-test"));
        assert_eq!(status.active_thinking_level.as_deref(), Some("low"));
    }

    #[test]
    fn model_preserves_provider_reasoning_metadata() {
        let models: ChatGptOAuthModels = serde_json::from_str(
            r#"{"models":[{"id":"gpt-test","name":"GPT Test","supportedReasoningLevels":[{"effort":"low","description":"Fast"}],"defaultReasoningLevel":"low"}]}"#,
        )
        .expect("model capabilities should deserialize");
        assert_eq!(models.models[0].supported_reasoning_levels[0].effort, "low");
        assert_eq!(models.models[0].default_reasoning_level.as_deref(), Some("low"));
    }

    #[test]
    fn authorization_url_is_limited_to_official_openai_endpoint() {
        let valid = "https://auth.openai.com/api/accounts/authorize?state=offline-test&hint=opaque";
        assert_eq!(validate_authorization_url(valid).as_deref(), Ok(valid));

        for invalid in [
            "http://auth.openai.com/api/accounts/authorize?state=test",
            "https://auth.openai.com.evil.test/api/accounts/authorize?state=test",
            "https://auth.openai.com/api/accounts/authorize/extra?state=test",
            "https://user@auth.openai.com/api/accounts/authorize?state=test",
            "https://example.test/api/accounts/authorize?state=test",
        ] {
            assert!(validate_authorization_url(invalid).is_err());
        }
    }
}
