use serde::Deserialize;
use serde_json::Value;
use sha2::{Digest, Sha256};

const PAGE_SCHEMA: &str = "vrcforge.chat_snapshot_page.v1";

#[derive(Debug, Deserialize)]
#[serde(rename_all = "camelCase")]
pub(crate) struct ChatSnapshotPage {
    schema: String,
    text: String,
    text_offset: usize,
    next_offset: Option<usize>,
    total_characters: usize,
    has_more: bool,
    snapshot_digest: String,
}

#[derive(Debug)]
pub(crate) struct ChatSnapshotAssembler {
    snapshot_digest: Option<String>,
    total_characters: Option<usize>,
    next_offset: usize,
    text: String,
    finished: bool,
}

impl ChatSnapshotAssembler {
    pub(crate) fn new() -> Self {
        Self {
            snapshot_digest: None,
            total_characters: None,
            next_offset: 0,
            text: String::new(),
            finished: false,
        }
    }

    pub(crate) fn append_page(&mut self, page: ChatSnapshotPage) -> Result<bool, String> {
        if self.finished {
            return Err("chat snapshot page arrived after terminal page".to_string());
        }
        if page.schema != PAGE_SCHEMA {
            return Err("chat snapshot page schema is invalid".to_string());
        }
        if page.snapshot_digest.len() != 64
            || !page
                .snapshot_digest
                .chars()
                .all(|character| character.is_ascii_hexdigit())
        {
            return Err("chat snapshot digest is invalid".to_string());
        }
        if page.text_offset != self.next_offset {
            return Err("chat snapshot page offset is not contiguous".to_string());
        }
        if page.total_characters == 0 && !page.text.is_empty() {
            return Err("chat snapshot page total is invalid".to_string());
        }
        if let Some(total) = self.total_characters {
            if total != page.total_characters {
                return Err("chat snapshot page total changed".to_string());
            }
        } else {
            self.total_characters = Some(page.total_characters);
        }
        if let Some(digest) = self.snapshot_digest.as_deref() {
            if digest != page.snapshot_digest {
                return Err("chat snapshot digest changed".to_string());
            }
        } else {
            self.snapshot_digest = Some(page.snapshot_digest.clone());
        }
        let page_characters = page.text.chars().count();
        let end = page
            .text_offset
            .checked_add(page_characters)
            .ok_or_else(|| "chat snapshot page offset overflow".to_string())?;
        if end > page.total_characters || (page.has_more && page_characters == 0) {
            return Err("chat snapshot page made no valid progress".to_string());
        }
        if page.has_more {
            let next = page
                .next_offset
                .ok_or_else(|| "chat snapshot continuation offset is missing".to_string())?;
            if next != end || next <= page.text_offset || next > page.total_characters {
                return Err("chat snapshot continuation offset is invalid".to_string());
            }
            self.next_offset = next;
        } else {
            if page.next_offset.is_some() || end != page.total_characters {
                return Err("chat snapshot terminal page is incomplete".to_string());
            }
            self.next_offset = end;
            self.finished = true;
        }
        self.text.push_str(&page.text);
        Ok(self.finished)
    }

    pub(crate) fn finish(self) -> Result<Value, String> {
        if !self.finished {
            return Err("chat snapshot ended before terminal page".to_string());
        }
        let digest = self
            .snapshot_digest
            .ok_or_else(|| "chat snapshot digest is missing".to_string())?;
        let mut hasher = Sha256::new();
        hasher.update(self.text.as_bytes());
        let actual = hasher
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect::<String>();
        if actual != digest {
            return Err("chat snapshot digest does not match assembled text".to_string());
        }
        serde_json::from_str(&self.text)
            .map_err(|error| format!("chat snapshot JSON is invalid: {error}"))
    }

    pub(crate) fn next_request(&self) -> Result<(usize, String), String> {
        if self.finished {
            return Err("chat snapshot is already complete".to_string());
        }
        Ok((
            self.next_offset,
            self.snapshot_digest.clone().unwrap_or_default(),
        ))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn page(
        text: &str,
        offset: usize,
        next: Option<usize>,
        total: usize,
        more: bool,
        digest: &str,
    ) -> ChatSnapshotPage {
        ChatSnapshotPage {
            schema: PAGE_SCHEMA.to_string(),
            text: text.to_string(),
            text_offset: offset,
            next_offset: next,
            total_characters: total,
            has_more: more,
            snapshot_digest: digest.to_string(),
        }
    }

    fn digest(text: &str) -> String {
        let mut hasher = Sha256::new();
        hasher.update(text.as_bytes());
        hasher
            .finalize()
            .iter()
            .map(|byte| format!("{byte:02x}"))
            .collect()
    }

    #[test]
    fn assembles_unicode_by_codepoint_offset_and_verifies_digest() {
        let text = r#"{"chats":[{"title":"你好🌸"}]}"#;
        let digest = digest(text);
        let mut assembler = ChatSnapshotAssembler::new();
        let split = text.find('🌸').unwrap();
        let first_len = text[..split].chars().count();
        assert!(!assembler
            .append_page(page(
                &text[..split],
                0,
                Some(first_len),
                text.chars().count(),
                true,
                &digest
            ))
            .unwrap());
        let tail = &text[split..];
        assert!(assembler
            .append_page(page(
                tail,
                first_len,
                None,
                text.chars().count(),
                false,
                &digest
            ))
            .unwrap());
        assert_eq!(assembler.finish().unwrap()["chats"][0]["title"], "你好🌸");
    }

    #[test]
    fn rejects_changed_digest_offset_and_incomplete_terminal() {
        let text = "{\"chats\":[]}";
        let digest = digest(text);
        let mut changed = ChatSnapshotAssembler::new();
        changed
            .append_page(page("{", 0, Some(1), text.chars().count(), true, &digest))
            .unwrap();
        assert!(changed
            .append_page(page(
                "\"",
                1,
                Some(2),
                text.chars().count(),
                true,
                &"0".repeat(64)
            ))
            .is_err());

        let mut offset = ChatSnapshotAssembler::new();
        assert!(offset
            .append_page(page("{", 1, None, text.chars().count(), false, &digest))
            .is_err());

        let mut incomplete = ChatSnapshotAssembler::new();
        incomplete
            .append_page(page("{", 0, Some(1), text.chars().count(), true, &digest))
            .unwrap();
        assert!(incomplete.finish().is_err());

        let mut altered = ChatSnapshotAssembler::new();
        altered
            .append_page(page(
                "{\"chats\":{}}",
                0,
                None,
                text.chars().count(),
                false,
                &digest,
            ))
            .unwrap();
        assert!(altered.finish().is_err());
    }
}
