#[path = "../primitive_evidence_authority_client.rs"]
mod primitive_evidence_authority_client;
#[allow(dead_code)]
#[path = "../primitive_evidence_authority_windows.rs"]
mod primitive_evidence_authority_windows;

use primitive_evidence_authority_client::{
    AuthorityClientCommand, AuthorityClientError, AuthorityClientExchange,
    ExternalModelPartHandleTokens, InstalledAuthorityClient,
};
use serde::Serialize;
use serde_json::json;
use std::{ffi::OsString, fmt, io::Write};

const CONTROLLER_EXCHANGE_SCHEMA: &str = "vrcforge.primitive_evidence_controller_exchange.v1";
const CONTROLLER_ERROR_SCHEMA: &str = "vrcforge.primitive_evidence_controller_error.v1";
const CONTROLLER_MODEL_PART_PRODUCTION_ADMISSION_CLOSED: &str =
    "controller_model_part_production_admission_closed";
const CONTROLLER_HANDLE_ADMISSION_ALREADY_CONSUMED: &str =
    "controller_handle_admission_already_consumed";
const CONTROLLER_HANDLE_ADMISSION_INVALID: &str = "controller_handle_admission_invalid";

fn main() {
    let arguments: Vec<OsString> = std::env::args_os().skip(1).collect();
    if let Err(error) = run(&arguments) {
        eprintln!(
            "{}",
            json!({
                "error": error.code(),
                "ok": false,
                "schema": CONTROLLER_ERROR_SCHEMA,
                "win32": error.win32(),
            })
        );
        std::process::exit(2);
    }
}

fn run(arguments: &[OsString]) -> Result<(), AuthorityClientError> {
    let parsed = parse_command(arguments)?;
    parsed.command.validate()?;
    let authorization = if parsed.requires_run_authorization() {
        Some(production_run_exchange_authorization()?)
    } else {
        None
    };
    let exchange = parsed.exchange_with(authorization.as_ref(), |command| {
        InstalledAuthorityClient::connect()?.execute(command)
    })?;
    let mut output = serialize_exchange(&exchange)?.into_bytes();
    output.push(b'\n');
    let mut stdout = std::io::stdout().lock();
    stdout
        .write_all(&output)
        .and_then(|()| stdout.flush())
        .map_err(|_| AuthorityClientError::from_code("controller_exchange_write_failed"))?;
    Ok(())
}

fn parse_command(arguments: &[OsString]) -> Result<ParsedControllerCommand, AuthorityClientError> {
    parse_command_with(arguments, |pending| pending.consume())
}

fn parse_command_with<A>(
    arguments: &[OsString],
    admission: A,
) -> Result<ParsedControllerCommand, AuthorityClientError>
where
    A: FnOnce(
        &mut PendingControllerExternalHandleAdmission,
    ) -> Result<ParsedExternalHandleAdmission, AuthorityClientError>,
{
    let mut admission = Some(admission);
    match arguments {
        [command] if command == "--status" => Ok(ParsedControllerCommand {
            command: AuthorityClientCommand::Status,
            external_admission: None,
        }),
        [command] if command == "--self-test" => Ok(ParsedControllerCommand {
            command: AuthorityClientCommand::SelfTest,
            external_admission: None,
        }),
        [command, request_id, generation_sha256, transaction_sha256, expected_external_binding_sha256, desktop, backend, unity, bridge_listener, fixture_contract, fixture_baseline]
            if command == "--run-model-part-composition" =>
        {
            let mut pending = PendingControllerExternalHandleAdmission::try_new(
                [
                    desktop,
                    backend,
                    unity,
                    bridge_listener,
                    fixture_contract,
                    fixture_baseline,
                ],
                generation_sha256,
                transaction_sha256,
                expected_external_binding_sha256,
            )?;
            let external_admission =
                admission
                    .take()
                    .expect("run admission closure is consumed once")(&mut pending)?;
            let handle_tokens = external_admission.token_projection();
            Ok(ParsedControllerCommand {
                command: AuthorityClientCommand::RunModelPartComposition {
                    request_id: unicode_request_id(request_id)?,
                    handle_tokens,
                },
                external_admission: Some(external_admission),
            })
        }
        [command, request_id] if command == "--cancel" => Ok(ParsedControllerCommand {
            command: AuthorityClientCommand::Cancel {
                request_id: unicode_request_id(request_id)?,
            },
            external_admission: None,
        }),
        [command, request_id] if command == "--get-result" => Ok(ParsedControllerCommand {
            command: AuthorityClientCommand::GetResult {
                request_id: unicode_request_id(request_id)?,
            },
            external_admission: None,
        }),
        _ => Err(AuthorityClientError::from_code(
            "controller_argument_rejected",
        )),
    }
}

#[derive(Debug)]
struct ParsedControllerCommand {
    command: AuthorityClientCommand,
    external_admission: Option<ParsedExternalHandleAdmission>,
}

impl ParsedControllerCommand {
    fn requires_run_authorization(&self) -> bool {
        matches!(
            self.command,
            AuthorityClientCommand::RunModelPartComposition { .. }
        )
    }

    fn exchange_with<R, F>(
        self,
        run_authorization: Option<&ControllerRunExchangeAuthorization>,
        exchange: F,
    ) -> Result<R, AuthorityClientError>
    where
        F: FnOnce(AuthorityClientCommand) -> Result<R, AuthorityClientError>,
    {
        match (&self.command, &self.external_admission) {
            (
                AuthorityClientCommand::RunModelPartComposition { handle_tokens, .. },
                Some(ParsedExternalHandleAdmission::Observed(admission)),
            ) if *handle_tokens == admission.token_projection()
                && admission.has_complete_retained_binding() =>
            {
                let _authorization = run_authorization.ok_or_else(|| {
                    AuthorityClientError::from_code(
                        CONTROLLER_MODEL_PART_PRODUCTION_ADMISSION_CLOSED,
                    )
                })?;
                let result = exchange(self.command)?;
                // This post-exchange read is intentional: the non-Clone
                // admission carrier must remain alive until the one client
                // exchange has completed, rather than being reduced to raw
                // tokens before the protected service sees the command.
                if !admission.has_complete_retained_binding() {
                    return Err(AuthorityClientError::from_code(
                        CONTROLLER_HANDLE_ADMISSION_INVALID,
                    ));
                }
                Ok(result)
            }
            (AuthorityClientCommand::RunModelPartComposition { .. }, _) => Err(
                AuthorityClientError::from_code(CONTROLLER_HANDLE_ADMISSION_INVALID),
            ),
            (_, None) if run_authorization.is_none() => exchange(self.command),
            (_, Some(_)) => Err(AuthorityClientError::from_code(
                CONTROLLER_HANDLE_ADMISSION_INVALID,
            )),
            (_, None) => Err(AuthorityClientError::from_code(
                CONTROLLER_HANDLE_ADMISSION_INVALID,
            )),
        }
    }
}

/// Affine authorization for the run exchange. Its production constructor
/// stays closed until the parent launcher and service fixed-eight admission
/// are composed into one authenticated call chain.
struct ControllerRunExchangeAuthorization {
    _private: (),
}

fn production_run_exchange_authorization(
) -> Result<ControllerRunExchangeAuthorization, AuthorityClientError> {
    Err(AuthorityClientError::from_code(
        CONTROLLER_MODEL_PART_PRODUCTION_ADMISSION_CLOSED,
    ))
}

#[cfg(test)]
impl ControllerRunExchangeAuthorization {
    fn for_test() -> Self {
        Self { _private: () }
    }
}

#[derive(Debug)]
enum ParsedExternalHandleAdmission {
    Observed(ObservedControllerExternalHandleAdmission),
    #[cfg(test)]
    ShapeOnly(ExternalModelPartHandleTokens),
}

impl ParsedExternalHandleAdmission {
    fn token_projection(&self) -> ExternalModelPartHandleTokens {
        match self {
            Self::Observed(admission) => admission.token_projection(),
            #[cfg(test)]
            Self::ShapeOnly(tokens) => *tokens,
        }
    }
}

struct ObservedControllerExternalHandleAdmission {
    tokens: ExternalModelPartHandleTokens,
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    object_identities: [[u8; 32]; 6],
    role_bindings: [[u8; 32]; 6],
    binding_sha256: [u8; 32],
}

impl ObservedControllerExternalHandleAdmission {
    fn token_projection(&self) -> ExternalModelPartHandleTokens {
        self.tokens
    }

    fn has_complete_retained_binding(&self) -> bool {
        !is_zero_digest(&self.generation_sha256)
            && !is_zero_digest(&self.transaction_sha256)
            && self.generation_sha256 != self.transaction_sha256
            && !is_zero_digest(&self.binding_sha256)
            && distinct_nonzero_digests(&self.object_identities)
            && distinct_nonzero_digests(&self.role_bindings)
    }
}

impl fmt::Debug for ObservedControllerExternalHandleAdmission {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ObservedControllerExternalHandleAdmission")
            .field(
                "roles",
                &[
                    "desktop",
                    "backend",
                    "unity",
                    "bridge_listener",
                    "fixture_contract",
                    "fixture_baseline",
                ],
            )
            .field("generation_sha256", &"<redacted>")
            .field("transaction_sha256", &"<redacted>")
            .field("object_identities", &"<redacted>")
            .field("role_bindings", &"<redacted>")
            .field("binding_sha256", &"<redacted>")
            .finish_non_exhaustive()
    }
}

struct PendingControllerExternalHandleAdmission {
    tokens: Option<ExternalModelPartHandleTokens>,
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
    expected_external_binding_sha256: [u8; 32],
}

impl PendingControllerExternalHandleAdmission {
    fn try_new(
        values: [&OsString; 6],
        generation_sha256: &OsString,
        transaction_sha256: &OsString,
        expected_external_binding_sha256: &OsString,
    ) -> Result<Self, AuthorityClientError> {
        let values = values.map(unicode_request_id);
        let values = values
            .into_iter()
            .collect::<Result<Vec<_>, _>>()?
            .try_into()
            .map_err(|_| AuthorityClientError::from_code(CONTROLLER_HANDLE_ADMISSION_INVALID))?;
        let tokens = ExternalModelPartHandleTokens::try_from_wire_values(values)
            .map_err(|_| AuthorityClientError::from_code(CONTROLLER_HANDLE_ADMISSION_INVALID))?;
        let generation_sha256 = canonical_sha256(generation_sha256)?;
        let transaction_sha256 = canonical_sha256(transaction_sha256)?;
        let expected_external_binding_sha256 = canonical_sha256(expected_external_binding_sha256)?;
        if generation_sha256 == transaction_sha256 {
            return Err(AuthorityClientError::from_code(
                CONTROLLER_HANDLE_ADMISSION_INVALID,
            ));
        }
        Ok(Self {
            tokens: Some(tokens),
            generation_sha256,
            transaction_sha256,
            expected_external_binding_sha256,
        })
    }

    fn consume(&mut self) -> Result<ParsedExternalHandleAdmission, AuthorityClientError> {
        // Burn first. Any failed validation is terminal for this process-local
        // admission and can never be retried with a replaced handle value.
        let tokens = self.tokens.take().ok_or_else(|| {
            AuthorityClientError::from_code(CONTROLLER_HANDLE_ADMISSION_ALREADY_CONSUMED)
        })?;
        let admitted = tokens
            .admit_inherited(
                self.generation_sha256,
                self.transaction_sha256,
                self.expected_external_binding_sha256,
            )
            .map_err(|_| AuthorityClientError::from_code(CONTROLLER_HANDLE_ADMISSION_INVALID))?;
        let binding = admitted.binding();
        let context = binding.context();
        let observed = ObservedControllerExternalHandleAdmission {
            tokens: admitted.token_projection(),
            generation_sha256: *context.generation_sha256(),
            transaction_sha256: *context.transaction_sha256(),
            object_identities: *binding.object_identities(),
            role_bindings: *binding.role_bindings(),
            binding_sha256: *binding.binding_sha256(),
        };
        if observed.binding_sha256 != self.expected_external_binding_sha256
            || !observed.has_complete_retained_binding()
        {
            return Err(AuthorityClientError::from_code(
                CONTROLLER_HANDLE_ADMISSION_INVALID,
            ));
        }
        Ok(ParsedExternalHandleAdmission::Observed(observed))
    }

    #[cfg(test)]
    fn consume_shape_only_for_test(
        &mut self,
    ) -> Result<ParsedExternalHandleAdmission, AuthorityClientError> {
        self.tokens
            .take()
            .map(ParsedExternalHandleAdmission::ShapeOnly)
            .ok_or_else(|| {
                AuthorityClientError::from_code(CONTROLLER_HANDLE_ADMISSION_ALREADY_CONSUMED)
            })
    }
}

impl fmt::Debug for PendingControllerExternalHandleAdmission {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("PendingControllerExternalHandleAdmission")
            .field(
                "roles",
                &[
                    "desktop",
                    "backend",
                    "unity",
                    "bridge_listener",
                    "fixture_contract",
                    "fixture_baseline",
                ],
            )
            .field("pending", &self.tokens.is_some())
            .field("generation_sha256", &"<redacted>")
            .field("transaction_sha256", &"<redacted>")
            .field("expected_external_binding_sha256", &"<redacted>")
            .finish()
    }
}

fn is_zero_digest(digest: &[u8; 32]) -> bool {
    digest.iter().all(|byte| *byte == 0)
}

fn distinct_nonzero_digests<const COUNT: usize>(digests: &[[u8; 32]; COUNT]) -> bool {
    digests.iter().enumerate().all(|(index, digest)| {
        !is_zero_digest(digest) && digests[..index].iter().all(|prior| prior != digest)
    })
}

fn canonical_sha256(value: &OsString) -> Result<[u8; 32], AuthorityClientError> {
    let value = value
        .to_str()
        .ok_or_else(|| AuthorityClientError::from_code(CONTROLLER_HANDLE_ADMISSION_INVALID))?;
    if value.len() != 64
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(AuthorityClientError::from_code(
            CONTROLLER_HANDLE_ADMISSION_INVALID,
        ));
    }
    let mut digest = [0u8; 32];
    for (index, chunk) in value.as_bytes().chunks_exact(2).enumerate() {
        digest[index] = (hex_nibble(chunk[0])? << 4) | hex_nibble(chunk[1])?;
    }
    if digest.iter().all(|byte| *byte == 0) {
        return Err(AuthorityClientError::from_code(
            CONTROLLER_HANDLE_ADMISSION_INVALID,
        ));
    }
    Ok(digest)
}

fn hex_nibble(value: u8) -> Result<u8, AuthorityClientError> {
    match value {
        b'0'..=b'9' => Ok(value - b'0'),
        b'a'..=b'f' => Ok(value - b'a' + 10),
        _ => Err(AuthorityClientError::from_code(
            CONTROLLER_HANDLE_ADMISSION_INVALID,
        )),
    }
}

fn unicode_request_id(value: &OsString) -> Result<String, AuthorityClientError> {
    value
        .to_str()
        .map(str::to_owned)
        .ok_or_else(|| AuthorityClientError::from_code("controller_argument_rejected"))
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
struct ControllerExchangeOutput<'a> {
    schema: &'static str,
    command: &'static str,
    requires_upper_layer_verification: bool,
    handshake_raw_json: &'a str,
    response_raw_json: &'a str,
}

fn serialize_exchange(exchange: &AuthorityClientExchange) -> Result<String, AuthorityClientError> {
    let handshake_raw_json = std::str::from_utf8(exchange.handshake_raw_bytes())
        .map_err(|_| AuthorityClientError::from_code("controller_exchange_encoding_failed"))?;
    let response_raw_json = std::str::from_utf8(exchange.response_raw_bytes())
        .map_err(|_| AuthorityClientError::from_code("controller_exchange_encoding_failed"))?;
    serde_json::to_string(&ControllerExchangeOutput {
        schema: CONTROLLER_EXCHANGE_SCHEMA,
        command: exchange.command(),
        requires_upper_layer_verification: true,
        handshake_raw_json,
        response_raw_json,
    })
    .map_err(|_| AuthorityClientError::from_code("controller_exchange_encoding_failed"))
}

#[cfg(test)]
mod tests {
    use super::*;

    const GENERATION: [u8; 32] = [0x71; 32];
    const TRANSACTION: [u8; 32] = [0x72; 32];
    const EXPECTED: [u8; 32] = [0x73; 32];

    fn arguments(values: &[&str]) -> Vec<OsString> {
        values.iter().map(OsString::from).collect()
    }

    fn digest(value: [u8; 32]) -> String {
        value.iter().map(|byte| format!("{byte:02x}")).collect()
    }

    fn run_arguments(request_id: &str) -> Vec<OsString> {
        arguments(&[
            "--run-model-part-composition",
            request_id,
            &digest(GENERATION),
            &digest(TRANSACTION),
            &digest(EXPECTED),
            "0000000000000011",
            "0000000000000022",
            "0000000000000033",
            "0000000000000044",
            "0000000000000055",
            "0000000000000066",
        ])
    }

    fn parse_shape(arguments: &[OsString]) -> Result<AuthorityClientCommand, AuthorityClientError> {
        parse_command_with(arguments, |pending| pending.consume_shape_only_for_test())
            .map(|parsed| parsed.command)
    }

    #[test]
    fn cli_accepts_only_the_five_fixed_typed_commands() {
        let cases = [
            (arguments(&["--status"]), "status", None),
            (arguments(&["--self-test"]), "selfTest", None),
            (
                run_arguments("request-1"),
                "runModelPartComposition",
                Some("request-1"),
            ),
            (
                arguments(&["--cancel", "request-2"]),
                "cancel",
                Some("request-2"),
            ),
            (
                arguments(&["--get-result", "request-3"]),
                "getResult",
                Some("request-3"),
            ),
        ];
        for (arguments, expected_name, expected_request_id) in cases {
            let command = parse_shape(&arguments).unwrap();
            command.validate().unwrap();
            assert_eq!(command.command_name(), expected_name);
            let observed_request_id = match &command {
                AuthorityClientCommand::RunModelPartComposition { request_id, .. }
                | AuthorityClientCommand::Cancel { request_id }
                | AuthorityClientCommand::GetResult { request_id } => Some(request_id.as_str()),
                AuthorityClientCommand::Status | AuthorityClientCommand::SelfTest => None,
            };
            assert_eq!(observed_request_id, expected_request_id);
        }
    }

    #[test]
    fn cli_rejects_raw_json_signing_extra_and_unbound_run_surfaces() {
        for rejected in [
            arguments(&[]),
            arguments(&["--request", "{}"]),
            arguments(&["--raw", "{}"]),
            arguments(&["--sign", "00"]),
            arguments(&["--finalize", "evidence"]),
            arguments(&["--status", "extra"]),
            arguments(&["--run-model-part-composition", "request-1"]),
            arguments(&["--cancel"]),
            arguments(&["--get-result", "request-1", "extra"]),
        ] {
            assert_eq!(
                parse_shape(&rejected).unwrap_err().code(),
                "controller_argument_rejected"
            );
        }
    }

    #[test]
    fn run_cli_requires_exactly_six_distinct_canonical_handle_tokens() {
        let command = parse_shape(&run_arguments("request-1")).unwrap();
        let AuthorityClientCommand::RunModelPartComposition { handle_tokens, .. } = command else {
            panic!("run command expected");
        };
        assert_eq!(handle_tokens.values(), [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]);

        let mut duplicate = run_arguments("request-1");
        duplicate[10] = duplicate[5].clone();
        assert_eq!(
            parse_shape(&duplicate).unwrap_err().code(),
            CONTROLLER_HANDLE_ADMISSION_INVALID
        );

        let mut uppercase = run_arguments("request-1");
        uppercase[10] = OsString::from("00000000000000AA");
        assert_eq!(
            parse_shape(&uppercase).unwrap_err().code(),
            CONTROLLER_HANDLE_ADMISSION_INVALID
        );
    }

    #[test]
    fn run_cli_rejects_missing_extra_and_legacy_eight_handle_counts() {
        let canonical = run_arguments("request-1");
        for token_count in [0usize, 5, 7, 8] {
            let mut values = canonical[..5].to_vec();
            values.extend(canonical.iter().skip(5).take(token_count).cloned());
            while values.len() < token_count + 5 {
                values.push(OsString::from(format!("{:016x}", values.len() + 1)));
            }
            assert_eq!(
                parse_shape(&values).unwrap_err().code(),
                "controller_argument_rejected"
            );
        }
    }

    #[test]
    fn run_cli_rejects_noncanonical_or_drifting_commitment_shape() {
        let mut uppercase = run_arguments("request-1");
        uppercase[2] = OsString::from("AA".repeat(32));
        assert_eq!(
            parse_shape(&uppercase).unwrap_err().code(),
            CONTROLLER_HANDLE_ADMISSION_INVALID
        );

        let mut zero = run_arguments("request-1");
        zero[4] = OsString::from("00".repeat(32));
        assert_eq!(
            parse_shape(&zero).unwrap_err().code(),
            CONTROLLER_HANDLE_ADMISSION_INVALID
        );

        let mut same_context = run_arguments("request-1");
        same_context[3] = same_context[2].clone();
        assert_eq!(
            parse_shape(&same_context).unwrap_err().code(),
            CONTROLLER_HANDLE_ADMISSION_INVALID
        );
    }

    #[test]
    fn cli_rejects_invalid_request_ids_before_connecting() {
        for request_id in ["", "-bad", "bad space", &"x".repeat(129)] {
            let command = parse_shape(&arguments(&["--get-result", request_id])).unwrap();
            assert_eq!(
                command.validate().unwrap_err().code(),
                "authority_client_request_id_invalid"
            );
        }
    }

    #[test]
    fn pending_admission_burns_before_validation_and_cannot_be_reused() {
        let arguments = run_arguments("request-1");
        let mut pending = PendingControllerExternalHandleAdmission::try_new(
            [
                &arguments[5],
                &arguments[6],
                &arguments[7],
                &arguments[8],
                &arguments[9],
                &arguments[10],
            ],
            &arguments[2],
            &arguments[3],
            &arguments[4],
        )
        .unwrap();
        let first = pending.consume();
        assert!(first.is_err());
        assert_eq!(
            pending.consume().unwrap_err().code(),
            CONTROLLER_HANDLE_ADMISSION_ALREADY_CONSUMED
        );
        let debug = format!("{pending:?}");
        assert!(!debug.contains("0000000000000011"));
        assert!(!debug.contains(&digest(GENERATION)));
    }

    #[test]
    fn source_has_no_environment_or_path_reopen_handle_fallback() {
        let source = include_str!("vrcforge_primitive_evidence_controller.rs");
        let handle_source = include_str!("../primitive_evidence_authority_pipe/handle_tokens.rs");
        for prohibited in [
            ["std::env::", "var"].concat(),
            ["--handle", "-path"].concat(),
            ["--driver", "-path"].concat(),
            ["--bridge-launcher", "-path"].concat(),
        ] {
            assert!(!source.contains(&prohibited));
            assert!(!handle_source.contains(&prohibited));
        }
        for reopen_api in [
            ["Create", "FileW"].concat(),
            ["GetFinalPathName", "ByHandleW"].concat(),
            ["Open", "Options"].concat(),
        ] {
            assert!(!handle_source.contains(&reopen_api));
        }
    }

    #[cfg(windows)]
    mod windows_tests {
        use super::*;
        use std::{
            fs::{self, File, OpenOptions},
            io::Write,
            os::windows::{
                fs::OpenOptionsExt,
                io::{AsRawHandle, FromRawHandle, RawHandle},
            },
            path::PathBuf,
            sync::atomic::{AtomicU64, Ordering},
        };
        use windows_sys::Win32::{
            Foundation::{CloseHandle, SetHandleInformation, HANDLE_FLAG_INHERIT},
            Storage::FileSystem::{FILE_SHARE_READ, FILE_SHARE_WRITE},
            System::Threading::CreateEventW,
        };

        struct InheritedHandleFixture {
            root: PathBuf,
            paths: Vec<PathBuf>,
            files: Vec<File>,
        }

        impl InheritedHandleFixture {
            fn new(writable_index: Option<usize>) -> Self {
                static SEQUENCE: AtomicU64 = AtomicU64::new(1);
                let root = std::env::temp_dir().join(format!(
                    "vrcforge-controller-inherited-handles-{}-{}",
                    std::process::id(),
                    SEQUENCE.fetch_add(1, Ordering::Relaxed)
                ));
                fs::create_dir(&root).unwrap();
                let mut paths = Vec::new();
                let mut files = Vec::new();
                for index in 0..6 {
                    let path = root.join(format!("role-{index}.bin"));
                    let mut writer = OpenOptions::new()
                        .create_new(true)
                        .write(true)
                        .open(&path)
                        .unwrap();
                    writer.write_all(&[index as u8 + 1]).unwrap();
                    writer.flush().unwrap();
                    drop(writer);
                    let writable = writable_index == Some(index);
                    let file = OpenOptions::new()
                        .read(true)
                        .write(writable)
                        .share_mode(FILE_SHARE_READ | if writable { FILE_SHARE_WRITE } else { 0 })
                        .open(&path)
                        .unwrap();
                    assert_ne!(
                        unsafe {
                            SetHandleInformation(
                                file.as_raw_handle().cast(),
                                HANDLE_FLAG_INHERIT,
                                HANDLE_FLAG_INHERIT,
                            )
                        },
                        0
                    );
                    paths.push(path);
                    files.push(file);
                }
                Self { root, paths, files }
            }

            fn tokens(&self) -> ExternalModelPartHandleTokens {
                ExternalModelPartHandleTokens::try_from_values(
                    self.files
                        .iter()
                        .map(|file| file.as_raw_handle() as usize as u64)
                        .collect::<Vec<_>>()
                        .try_into()
                        .unwrap(),
                )
                .unwrap()
            }

            fn expected_binding(&self) -> [u8; 32] {
                self.tokens()
                    .inherited_binding_sha256_for_test(GENERATION, TRANSACTION)
                    .unwrap()
            }

            fn run_arguments(&self, expected: [u8; 32]) -> Vec<OsString> {
                let mut arguments = vec![
                    OsString::from("--run-model-part-composition"),
                    OsString::from("request-1"),
                    OsString::from(digest(GENERATION)),
                    OsString::from(digest(TRANSACTION)),
                    OsString::from(digest(expected)),
                ];
                arguments.extend(self.tokens().wire_values().into_iter().map(OsString::from));
                arguments
            }
        }

        impl Drop for InheritedHandleFixture {
            fn drop(&mut self) {
                self.files.clear();
                for path in &self.paths {
                    let _ = fs::remove_file(path);
                }
                let _ = fs::remove_dir(&self.root);
            }
        }

        #[test]
        fn exact_inherited_external_six_is_observed_but_production_send_stays_closed() {
            let fixture = InheritedHandleFixture::new(None);
            let arguments = fixture.run_arguments(fixture.expected_binding());
            let parsed = parse_command(&arguments).unwrap();
            let AuthorityClientCommand::RunModelPartComposition { handle_tokens, .. } =
                &parsed.command
            else {
                panic!("run command expected");
            };
            assert_eq!(handle_tokens.values(), fixture.tokens().values());
            let Some(ParsedExternalHandleAdmission::Observed(admission)) =
                &parsed.external_admission
            else {
                panic!("observed admission expected");
            };
            assert_eq!(
                admission.token_projection().values(),
                fixture.tokens().values()
            );
            assert_eq!(admission.generation_sha256, GENERATION);
            assert_eq!(admission.transaction_sha256, TRANSACTION);
            assert_eq!(admission.binding_sha256, fixture.expected_binding());
            assert!(admission.has_complete_retained_binding());
            let debug = format!("{parsed:?}");
            let sensitive_values = [
                fixture.tokens().wire_values()[0].clone(),
                digest(GENERATION),
                digest(TRANSACTION),
                digest(fixture.expected_binding()),
            ];
            for sensitive in sensitive_values {
                assert!(!debug.contains(&sensitive));
            }
            assert_eq!(
                run(&arguments).unwrap_err().code(),
                CONTROLLER_MODEL_PART_PRODUCTION_ADMISSION_CLOSED
            );
        }

        #[test]
        fn admitted_external_six_remains_live_through_one_test_exchange() {
            let fixture = InheritedHandleFixture::new(None);
            let parsed = parse_command(&fixture.run_arguments(fixture.expected_binding())).unwrap();
            let authorization = ControllerRunExchangeAuthorization::for_test();
            let exchanged = parsed
                .exchange_with(Some(&authorization), |command| {
                    let AuthorityClientCommand::RunModelPartComposition {
                        request_id,
                        handle_tokens,
                    } = command
                    else {
                        panic!("run command expected");
                    };
                    assert_eq!(request_id, "request-1");
                    assert_eq!(handle_tokens.values(), fixture.tokens().values());
                    Ok("exchange-complete")
                })
                .unwrap();
            assert_eq!(exchanged, "exchange-complete");
        }

        #[test]
        fn order_generation_and_transaction_drift_fail_closed() {
            let fixture = InheritedHandleFixture::new(None);
            let expected = fixture.expected_binding();

            let mut reordered = fixture.run_arguments(expected);
            reordered.swap(5, 6);
            assert_eq!(
                parse_command(&reordered).unwrap_err().code(),
                CONTROLLER_HANDLE_ADMISSION_INVALID
            );

            let mut generation = fixture.run_arguments(expected);
            generation[2] = OsString::from(digest([0x74; 32]));
            assert_eq!(
                parse_command(&generation).unwrap_err().code(),
                CONTROLLER_HANDLE_ADMISSION_INVALID
            );

            let mut transaction = fixture.run_arguments(expected);
            transaction[3] = OsString::from(digest([0x75; 32]));
            assert_eq!(
                parse_command(&transaction).unwrap_err().code(),
                CONTROLLER_HANDLE_ADMISSION_INVALID
            );
        }

        #[test]
        fn missing_inheritance_writable_access_type_and_identity_alias_fail_closed() {
            let fixture = InheritedHandleFixture::new(None);
            let expected = fixture.expected_binding();
            assert_ne!(
                unsafe {
                    SetHandleInformation(
                        fixture.files[2].as_raw_handle().cast(),
                        HANDLE_FLAG_INHERIT,
                        0,
                    )
                },
                0
            );
            assert_eq!(
                parse_command(&fixture.run_arguments(expected))
                    .unwrap_err()
                    .code(),
                CONTROLLER_HANDLE_ADMISSION_INVALID
            );

            let writable = InheritedHandleFixture::new(Some(3));
            assert!(writable
                .tokens()
                .inherited_binding_sha256_for_test(GENERATION, TRANSACTION)
                .is_err());

            let alias = InheritedHandleFixture::new(None);
            let expected = alias.expected_binding();
            let duplicate = alias.files[0].try_clone().unwrap();
            assert_ne!(
                unsafe {
                    SetHandleInformation(
                        duplicate.as_raw_handle().cast(),
                        HANDLE_FLAG_INHERIT,
                        HANDLE_FLAG_INHERIT,
                    )
                },
                0
            );
            let mut alias_values = alias.tokens().values();
            alias_values[5] = duplicate.as_raw_handle() as usize as u64;
            let aliased_tokens =
                ExternalModelPartHandleTokens::try_from_values(alias_values).unwrap();
            assert!(aliased_tokens
                .inherited_binding_sha256_for_test(GENERATION, TRANSACTION)
                .is_err());
            let mut alias_arguments = alias.run_arguments(expected);
            alias_arguments[10] = OsString::from(format!(
                "{:016x}",
                duplicate.as_raw_handle() as usize as u64
            ));
            assert_eq!(
                parse_command(&alias_arguments).unwrap_err().code(),
                CONTROLLER_HANDLE_ADMISSION_INVALID
            );

            let event_raw = unsafe { CreateEventW(std::ptr::null(), 1, 0, std::ptr::null()) };
            assert!(!event_raw.is_null());
            assert_ne!(
                unsafe {
                    SetHandleInformation(event_raw, HANDLE_FLAG_INHERIT, HANDLE_FLAG_INHERIT)
                },
                0
            );
            let event = unsafe { File::from_raw_handle(event_raw as RawHandle) };
            let type_fixture = InheritedHandleFixture::new(None);
            let expected = type_fixture.expected_binding();
            let mut type_arguments = type_fixture.run_arguments(expected);
            type_arguments[9] =
                OsString::from(format!("{:016x}", event.as_raw_handle() as usize as u64));
            assert_eq!(
                parse_command(&type_arguments).unwrap_err().code(),
                CONTROLLER_HANDLE_ADMISSION_INVALID
            );
            let raw = event.as_raw_handle();
            std::mem::forget(event);
            assert_ne!(unsafe { CloseHandle(raw.cast()) }, 0);
        }
    }
}
