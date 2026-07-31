use super::MAINTENANCE_JOURNAL_SCHEMA;
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};
use std::fmt;

const TERMINAL_RECEIPT_DOMAIN: &[u8] = b"vrcforge-authority-maintenance-terminal-receipt-v1\0";
const MAX_TERMINAL_RECEIPT_BYTES: usize = 64 * 1024;

#[derive(Debug, Clone, PartialEq, Eq)]
pub(super) struct MaintenanceJournalReceiptError(&'static str);

impl MaintenanceJournalReceiptError {
    pub(super) fn code(&self) -> &'static str {
        self.0
    }
}

impl fmt::Display for MaintenanceJournalReceiptError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.0)
    }
}

impl std::error::Error for MaintenanceJournalReceiptError {}

#[derive(Debug, Clone, Copy, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase")]
pub(super) enum MaintenanceJournalTerminalKind {
    Committed,
    RolledBack,
    Contained,
}

impl MaintenanceJournalTerminalKind {
    fn digest_tag(self) -> u8 {
        match self {
            Self::Committed => 1,
            Self::RolledBack => 2,
            Self::Contained => 3,
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize, PartialEq, Eq)]
#[serde(rename_all = "camelCase", deny_unknown_fields)]
pub(super) struct MaintenanceJournalTerminalReceipt {
    schema: String,
    generation: String,
    plan_sha256: String,
    transaction_sha256: String,
    activation_epoch: u64,
    terminal: MaintenanceJournalTerminalKind,
    terminal_sequence: u64,
    previous_transition_sha256: String,
    transition_chain_sha256: String,
    receipt_sha256: String,
}

impl MaintenanceJournalTerminalReceipt {
    #[allow(clippy::too_many_arguments)]
    pub(super) fn new(
        generation: [u8; 32],
        plan_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
        activation_epoch: u64,
        terminal: MaintenanceJournalTerminalKind,
        terminal_sequence: u64,
        previous_transition_sha256: [u8; 32],
        transition_chain_sha256: [u8; 32],
    ) -> Result<Self, MaintenanceJournalReceiptError> {
        validate_nonzero(&generation)?;
        validate_nonzero(&plan_sha256)?;
        validate_nonzero(&transaction_sha256)?;
        validate_nonzero(&previous_transition_sha256)?;
        validate_nonzero(&transition_chain_sha256)?;
        if activation_epoch == 0 || terminal_sequence == 0 {
            return Err(MaintenanceJournalReceiptError(
                "authority_maintenance_terminal_receipt_invalid",
            ));
        }
        let receipt_sha256 = terminal_receipt_digest(
            &generation,
            &plan_sha256,
            &transaction_sha256,
            activation_epoch,
            terminal,
            terminal_sequence,
            &previous_transition_sha256,
            &transition_chain_sha256,
        );
        Ok(Self {
            schema: MAINTENANCE_JOURNAL_SCHEMA.to_string(),
            generation: hex_lower(&generation),
            plan_sha256: hex_lower(&plan_sha256),
            transaction_sha256: hex_lower(&transaction_sha256),
            activation_epoch,
            terminal,
            terminal_sequence,
            previous_transition_sha256: hex_lower(&previous_transition_sha256),
            transition_chain_sha256: hex_lower(&transition_chain_sha256),
            receipt_sha256: hex_lower(&receipt_sha256),
        })
    }

    pub(super) fn parse_canonical(bytes: &[u8]) -> Result<Self, MaintenanceJournalReceiptError> {
        if bytes.is_empty() || bytes.len() > MAX_TERMINAL_RECEIPT_BYTES {
            return Err(MaintenanceJournalReceiptError(
                "authority_maintenance_terminal_receipt_size_invalid",
            ));
        }
        let value: Self = serde_json::from_slice(bytes).map_err(|_| {
            MaintenanceJournalReceiptError("authority_maintenance_terminal_receipt_invalid")
        })?;
        if serde_json::to_vec(&value).ok().as_deref() != Some(bytes) {
            return Err(MaintenanceJournalReceiptError(
                "authority_maintenance_terminal_receipt_noncanonical",
            ));
        }
        value.validate()?;
        Ok(value)
    }

    pub(super) fn canonical_bytes(&self) -> Result<Vec<u8>, MaintenanceJournalReceiptError> {
        self.validate()?;
        serde_json::to_vec(self).map_err(|_| {
            MaintenanceJournalReceiptError("authority_maintenance_terminal_receipt_invalid")
        })
    }

    pub(super) fn generation(&self) -> Result<[u8; 32], MaintenanceJournalReceiptError> {
        decode_digest(&self.generation)
    }

    pub(super) fn plan_sha256(&self) -> Result<[u8; 32], MaintenanceJournalReceiptError> {
        decode_digest(&self.plan_sha256)
    }

    pub(super) fn transaction_sha256(&self) -> Result<[u8; 32], MaintenanceJournalReceiptError> {
        decode_digest(&self.transaction_sha256)
    }

    pub(super) fn activation_epoch(&self) -> u64 {
        self.activation_epoch
    }

    pub(super) fn terminal(&self) -> MaintenanceJournalTerminalKind {
        self.terminal
    }

    pub(super) fn receipt_sha256(&self) -> Result<[u8; 32], MaintenanceJournalReceiptError> {
        decode_digest(&self.receipt_sha256)
    }

    fn validate(&self) -> Result<(), MaintenanceJournalReceiptError> {
        if self.schema != MAINTENANCE_JOURNAL_SCHEMA
            || self.activation_epoch == 0
            || self.terminal_sequence == 0
        {
            return Err(MaintenanceJournalReceiptError(
                "authority_maintenance_terminal_receipt_invalid",
            ));
        }
        let generation = decode_digest(&self.generation)?;
        let plan_sha256 = decode_digest(&self.plan_sha256)?;
        let transaction_sha256 = decode_digest(&self.transaction_sha256)?;
        let previous_transition_sha256 = decode_digest(&self.previous_transition_sha256)?;
        let transition_chain_sha256 = decode_digest(&self.transition_chain_sha256)?;
        let receipt_sha256 = decode_digest(&self.receipt_sha256)?;
        let expected = terminal_receipt_digest(
            &generation,
            &plan_sha256,
            &transaction_sha256,
            self.activation_epoch,
            self.terminal,
            self.terminal_sequence,
            &previous_transition_sha256,
            &transition_chain_sha256,
        );
        if receipt_sha256 != expected {
            return Err(MaintenanceJournalReceiptError(
                "authority_maintenance_terminal_receipt_digest_mismatch",
            ));
        }
        Ok(())
    }
}

#[allow(clippy::too_many_arguments)]
fn terminal_receipt_digest(
    generation: &[u8; 32],
    plan_sha256: &[u8; 32],
    transaction_sha256: &[u8; 32],
    activation_epoch: u64,
    terminal: MaintenanceJournalTerminalKind,
    terminal_sequence: u64,
    previous_transition_sha256: &[u8; 32],
    transition_chain_sha256: &[u8; 32],
) -> [u8; 32] {
    let mut digest = Sha256::new();
    digest.update(TERMINAL_RECEIPT_DOMAIN);
    digest.update(generation);
    digest.update(plan_sha256);
    digest.update(transaction_sha256);
    digest.update(activation_epoch.to_be_bytes());
    digest.update([terminal.digest_tag()]);
    digest.update(terminal_sequence.to_be_bytes());
    digest.update(previous_transition_sha256);
    digest.update(transition_chain_sha256);
    digest.finalize().into()
}

fn validate_nonzero(value: &[u8; 32]) -> Result<(), MaintenanceJournalReceiptError> {
    if value.iter().all(|byte| *byte == 0) {
        Err(MaintenanceJournalReceiptError(
            "authority_maintenance_terminal_receipt_invalid",
        ))
    } else {
        Ok(())
    }
}

fn decode_digest(value: &str) -> Result<[u8; 32], MaintenanceJournalReceiptError> {
    if value.len() != 64
        || value
            .as_bytes()
            .iter()
            .any(|byte| !matches!(byte, b'0'..=b'9' | b'a'..=b'f'))
    {
        return Err(MaintenanceJournalReceiptError(
            "authority_maintenance_terminal_receipt_invalid",
        ));
    }
    let mut output = [0u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        output[index] = (decode_nibble(chunk[0])? << 4) | decode_nibble(chunk[1])?;
    }
    validate_nonzero(&output)?;
    Ok(output)
}

fn decode_nibble(value: u8) -> Result<u8, MaintenanceJournalReceiptError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(MaintenanceJournalReceiptError(
            "authority_maintenance_terminal_receipt_invalid",
        )),
    }
}

fn hex_lower(value: &[u8]) -> String {
    const DIGITS: &[u8; 16] = b"0123456789abcdef";
    let mut output = String::with_capacity(value.len() * 2);
    for byte in value {
        output.push(DIGITS[(byte >> 4) as usize] as char);
        output.push(DIGITS[(byte & 0x0f) as usize] as char);
    }
    output
}

#[cfg(test)]
mod tests {
    use super::*;

    fn receipt(terminal: MaintenanceJournalTerminalKind) -> MaintenanceJournalTerminalReceipt {
        MaintenanceJournalTerminalReceipt::new(
            [0x11; 32], [0x22; 32], [0x33; 32], 1, terminal, 9, [0x44; 32], [0x55; 32],
        )
        .unwrap()
    }

    #[test]
    fn all_terminal_kinds_round_trip_canonically() {
        for terminal in [
            MaintenanceJournalTerminalKind::Committed,
            MaintenanceJournalTerminalKind::RolledBack,
            MaintenanceJournalTerminalKind::Contained,
        ] {
            let value = receipt(terminal);
            let bytes = value.canonical_bytes().unwrap();
            assert_eq!(
                MaintenanceJournalTerminalReceipt::parse_canonical(&bytes).unwrap(),
                value
            );
            assert_ne!(value.receipt_sha256().unwrap(), [0; 32]);
        }
    }

    #[test]
    fn unknown_noncanonical_and_self_filled_digests_are_rejected() {
        let value = receipt(MaintenanceJournalTerminalKind::Committed);
        let bytes = value.canonical_bytes().unwrap();

        let mut unknown: serde_json::Value = serde_json::from_slice(&bytes).unwrap();
        unknown["extra"] = serde_json::json!(true);
        assert!(MaintenanceJournalTerminalReceipt::parse_canonical(
            &serde_json::to_vec(&unknown).unwrap()
        )
        .is_err());

        let mut noncanonical = bytes.clone();
        noncanonical.push(b'\n');
        assert_eq!(
            MaintenanceJournalTerminalReceipt::parse_canonical(&noncanonical)
                .unwrap_err()
                .code(),
            "authority_maintenance_terminal_receipt_noncanonical"
        );

        let changed = String::from_utf8(bytes)
            .unwrap()
            .replace("\"terminalSequence\":9", "\"terminalSequence\":10")
            .into_bytes();
        assert_eq!(
            MaintenanceJournalTerminalReceipt::parse_canonical(&changed)
                .unwrap_err()
                .code(),
            "authority_maintenance_terminal_receipt_digest_mismatch"
        );
    }

    #[test]
    fn every_terminal_binding_field_is_digest_protected() {
        let original = receipt(MaintenanceJournalTerminalKind::Committed);
        let mut variants = Vec::new();

        let mut value = original.clone();
        value.generation = hex_lower(&[0x12; 32]);
        variants.push(value);
        let mut value = original.clone();
        value.plan_sha256 = hex_lower(&[0x23; 32]);
        variants.push(value);
        let mut value = original.clone();
        value.transaction_sha256 = hex_lower(&[0x34; 32]);
        variants.push(value);
        let mut value = original.clone();
        value.activation_epoch = 2;
        variants.push(value);
        let mut value = original.clone();
        value.terminal = MaintenanceJournalTerminalKind::Contained;
        variants.push(value);
        let mut value = original.clone();
        value.terminal_sequence = 10;
        variants.push(value);
        let mut value = original.clone();
        value.previous_transition_sha256 = hex_lower(&[0x45; 32]);
        variants.push(value);
        let mut value = original.clone();
        value.transition_chain_sha256 = hex_lower(&[0x56; 32]);
        variants.push(value);
        let mut value = original;
        value.receipt_sha256 = hex_lower(&[0x66; 32]);
        variants.push(value);

        for value in variants {
            assert_eq!(
                value.canonical_bytes().unwrap_err().code(),
                "authority_maintenance_terminal_receipt_digest_mismatch"
            );
        }
    }
}
