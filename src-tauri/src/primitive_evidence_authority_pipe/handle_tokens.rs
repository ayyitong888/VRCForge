use serde::{de, ser::SerializeSeq, Deserialize, Deserializer, Serialize, Serializer};
use sha2::{Digest, Sha256};
use std::fmt;

#[allow(dead_code)] // consumed only after the service composes protected roots
pub const FIXED_MODEL_PART_HANDLE_COUNT: usize = 8;
#[allow(dead_code)] // consumed only after the service composes protected roots
pub const FIXED_MODEL_PART_HANDLE_ROLES: [&str; FIXED_MODEL_PART_HANDLE_COUNT] = [
    "driver",
    "desktop",
    "backend",
    "unity",
    "bridge_launcher",
    "bridge_listener",
    "fixture_contract",
    "fixture_baseline",
];

pub const EXTERNAL_MODEL_PART_HANDLE_COUNT: usize = 6;
pub const EXTERNAL_MODEL_PART_HANDLE_ROLES: [&str; EXTERNAL_MODEL_PART_HANDLE_COUNT] = [
    "desktop",
    "backend",
    "unity",
    "bridge_listener",
    "fixture_contract",
    "fixture_baseline",
];
pub const EXTERNAL_MODEL_PART_FIXED_INDICES: [usize; EXTERNAL_MODEL_PART_HANDLE_COUNT] =
    [1, 2, 3, 5, 6, 7];

#[allow(dead_code)] // consumed only by the protected-root composition slice
pub const PROTECTED_MODEL_PART_HANDLE_COUNT: usize = 2;
#[allow(dead_code)] // consumed only by the protected-root composition slice
pub const PROTECTED_MODEL_PART_HANDLE_ROLES: [&str; PROTECTED_MODEL_PART_HANDLE_COUNT] =
    ["driver", "bridge_launcher"];
#[allow(dead_code)] // consumed only by the protected-root composition slice
pub const PROTECTED_MODEL_PART_FIXED_INDICES: [usize; PROTECTED_MODEL_PART_HANDLE_COUNT] = [0, 4];

const HANDLE_TOKEN_HEX_LENGTH: usize = 16;
const EXTERNAL_HANDLE_OBJECT_IDENTITY_DOMAIN: &[u8] =
    b"vrcforge-controller-external-handle-object-v1\0";
const EXTERNAL_HANDLE_CAPABILITY_BINDING_DOMAIN: &[u8] =
    b"vrcforge-controller-external-handle-capability-v1\0";
const EXTERNAL_HANDLE_SET_BINDING_DOMAIN: &[u8] = b"vrcforge-controller-external-handle-set-v1\0";
#[allow(dead_code)]
const PROTECTED_HANDLE_SET_BINDING_DOMAIN: &[u8] = b"vrcforge-service-protected-handle-set-v1\0";
#[allow(dead_code)]
const FIXED_HANDLE_SET_BINDING_DOMAIN: &[u8] = b"vrcforge-fixed-handle-set-v1\0";

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct ModelPartHandleBindingContext {
    generation_sha256: [u8; 32],
    transaction_sha256: [u8; 32],
}

impl ModelPartHandleBindingContext {
    pub fn try_new(
        generation_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
    ) -> Result<Self, ExternalModelPartHandleTokenError> {
        if is_zero_digest(&generation_sha256)
            || is_zero_digest(&transaction_sha256)
            || generation_sha256 == transaction_sha256
        {
            return Err(ExternalModelPartHandleTokenError);
        }
        Ok(Self {
            generation_sha256,
            transaction_sha256,
        })
    }

    pub fn generation_sha256(&self) -> &[u8; 32] {
        &self.generation_sha256
    }

    pub fn transaction_sha256(&self) -> &[u8; 32] {
        &self.transaction_sha256
    }
}

impl fmt::Debug for ModelPartHandleBindingContext {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ModelPartHandleBindingContext")
            .field("generation_sha256", &"<redacted>")
            .field("transaction_sha256", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct ExternalModelPartHandleBinding {
    context: ModelPartHandleBindingContext,
    object_identities: [[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    role_bindings: [[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    binding_sha256: [u8; 32],
}

impl ExternalModelPartHandleBinding {
    pub fn try_from_handle_bindings(
        context: ModelPartHandleBindingContext,
        object_identities: [[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT],
        role_bindings: [[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    ) -> Result<Self, ExternalModelPartHandleTokenError> {
        validate_distinct_nonzero_digests(&object_identities)?;
        validate_distinct_nonzero_digests(&role_bindings)?;
        let binding_sha256 =
            external_handle_set_binding_sha256(&context, &object_identities, &role_bindings);
        Ok(Self {
            context,
            object_identities,
            role_bindings,
            binding_sha256,
        })
    }

    #[allow(dead_code)]
    pub fn context(&self) -> ModelPartHandleBindingContext {
        self.context
    }

    #[allow(dead_code)]
    pub fn object_identities(&self) -> &[[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
        &self.object_identities
    }

    #[allow(dead_code)]
    pub fn role_bindings(&self) -> &[[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
        &self.role_bindings
    }

    pub fn binding_sha256(&self) -> &[u8; 32] {
        &self.binding_sha256
    }
}

impl fmt::Debug for ExternalModelPartHandleBinding {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExternalModelPartHandleBinding")
            .field("roles", &EXTERNAL_MODEL_PART_HANDLE_ROLES)
            .field("context", &self.context)
            .field("binding_sha256", &"<redacted>")
            .finish()
    }
}

/// Process-local proof that the controller observed the exact external-six
/// handles under one generation/transaction context. This carrier is
/// deliberately not `Copy` or `Clone`: the raw token projection is not an
/// admission proof, and production transport must retain this binding until
/// the service authenticates and composes its protected roots.
#[derive(PartialEq, Eq)]
pub struct AdmittedExternalModelPartHandles {
    tokens: ExternalModelPartHandleTokens,
    binding: ExternalModelPartHandleBinding,
}

impl AdmittedExternalModelPartHandles {
    pub fn token_projection(&self) -> ExternalModelPartHandleTokens {
        self.tokens
    }

    pub fn binding(&self) -> &ExternalModelPartHandleBinding {
        &self.binding
    }

    #[cfg(test)]
    #[allow(dead_code)] // shared across authority test hosts; unused in the controller test binary
    pub(crate) fn exact_test_fixture(
        generation_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
        seed: u8,
    ) -> Result<Self, ExternalModelPartHandleTokenError> {
        let seed = seed.max(1);
        let context =
            ModelPartHandleBindingContext::try_new(generation_sha256, transaction_sha256)?;
        let tokens =
            ExternalModelPartHandleTokens::try_from_values(std::array::from_fn(|index| {
                u64::from(seed) * 0x100 + index as u64 + 1
            }))?;
        let object_identities =
            std::array::from_fn(|index| [seed.wrapping_add(index as u8).max(1); 32]);
        let role_bindings = std::array::from_fn(|index| {
            [seed.wrapping_add(EXTERNAL_MODEL_PART_HANDLE_COUNT as u8)
                .wrapping_add(index as u8)
                .max(1); 32]
        });
        let binding = ExternalModelPartHandleBinding::try_from_handle_bindings(
            context,
            object_identities,
            role_bindings,
        )?;
        Ok(Self { tokens, binding })
    }
}

impl fmt::Debug for AdmittedExternalModelPartHandles {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("AdmittedExternalModelPartHandles")
            .field("roles", &EXTERNAL_MODEL_PART_HANDLE_ROLES)
            .field("binding", &self.binding)
            .finish_non_exhaustive()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)] // authenticated construction belongs to the service slice
pub struct ProtectedModelPartHandleBinding {
    context: ModelPartHandleBindingContext,
    object_identities: [[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT],
    role_bindings: [[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT],
    binding_sha256: [u8; 32],
}

#[allow(dead_code)] // authenticated construction belongs to the service slice
impl ProtectedModelPartHandleBinding {
    pub fn try_from_handle_bindings(
        context: ModelPartHandleBindingContext,
        object_identities: [[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT],
        role_bindings: [[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT],
    ) -> Result<Self, ExternalModelPartHandleTokenError> {
        validate_distinct_nonzero_digests(&object_identities)?;
        validate_distinct_nonzero_digests(&role_bindings)?;
        let mut hasher = Sha256::new();
        hasher.update(PROTECTED_HANDLE_SET_BINDING_DOMAIN);
        update_context(&mut hasher, &context);
        hasher.update((PROTECTED_MODEL_PART_HANDLE_COUNT as u64).to_be_bytes());
        for (((role, fixed_index), object_identity), binding) in PROTECTED_MODEL_PART_HANDLE_ROLES
            .iter()
            .zip(PROTECTED_MODEL_PART_FIXED_INDICES)
            .zip(object_identities)
            .zip(role_bindings)
        {
            update_role_binding(&mut hasher, fixed_index, role, &object_identity, &binding);
        }
        Ok(Self {
            context,
            object_identities,
            role_bindings,
            binding_sha256: hasher.finalize().into(),
        })
    }

    pub fn context(&self) -> ModelPartHandleBindingContext {
        self.context
    }

    pub fn object_identities(&self) -> &[[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT] {
        &self.object_identities
    }

    pub fn role_bindings(&self) -> &[[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT] {
        &self.role_bindings
    }

    pub fn binding_sha256(&self) -> &[u8; 32] {
        &self.binding_sha256
    }
}

impl fmt::Debug for ProtectedModelPartHandleBinding {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ProtectedModelPartHandleBinding")
            .field("roles", &PROTECTED_MODEL_PART_HANDLE_ROLES)
            .field("context", &self.context)
            .field("binding_sha256", &"<redacted>")
            .finish()
    }
}

/// Typed projection of the fixed eight role bindings. Construction proves
/// only that an observed external-six set and a separately produced protected-
/// two set share one generation/transaction context and the expected ordered
/// commitment. Authentication of the protected roots remains a service-side
/// FinalCommit responsibility.
#[derive(Clone, Copy, PartialEq, Eq)]
#[allow(dead_code)] // production composition remains closed in the service slice
pub struct FixedModelPartHandleComposition {
    context: ModelPartHandleBindingContext,
    object_identities: [[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT],
    role_bindings: [[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT],
    binding_sha256: [u8; 32],
}

#[allow(dead_code)] // production composition remains closed in the service slice
impl FixedModelPartHandleComposition {
    pub fn expected_binding_sha256(
        external: &ExternalModelPartHandleBinding,
        protected: &ProtectedModelPartHandleBinding,
    ) -> Result<[u8; 32], ExternalModelPartHandleTokenError> {
        let (context, object_identities, role_bindings) =
            compose_role_bindings(external, protected)?;
        Ok(fixed_handle_set_binding_sha256(
            &context,
            &object_identities,
            &role_bindings,
            external.binding_sha256(),
            protected.binding_sha256(),
        ))
    }

    pub fn compose(
        external: ExternalModelPartHandleBinding,
        protected: ProtectedModelPartHandleBinding,
        expected_binding_sha256: [u8; 32],
    ) -> Result<Self, ExternalModelPartHandleTokenError> {
        if is_zero_digest(&expected_binding_sha256) {
            return Err(ExternalModelPartHandleTokenError);
        }
        let (context, object_identities, role_bindings) =
            compose_role_bindings(&external, &protected)?;
        let binding_sha256 = fixed_handle_set_binding_sha256(
            &context,
            &object_identities,
            &role_bindings,
            external.binding_sha256(),
            protected.binding_sha256(),
        );
        if !constant_time_equal(&binding_sha256, &expected_binding_sha256) {
            return Err(ExternalModelPartHandleTokenError);
        }
        Ok(Self {
            context,
            object_identities,
            role_bindings,
            binding_sha256,
        })
    }

    pub fn context(&self) -> ModelPartHandleBindingContext {
        self.context
    }

    pub fn object_identities(&self) -> &[[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT] {
        &self.object_identities
    }

    pub fn role_bindings(&self) -> &[[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT] {
        &self.role_bindings
    }

    pub fn binding_sha256(&self) -> &[u8; 32] {
        &self.binding_sha256
    }
}

impl fmt::Debug for FixedModelPartHandleComposition {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("FixedModelPartHandleComposition")
            .field("roles", &FIXED_MODEL_PART_HANDLE_ROLES)
            .field("context", &self.context)
            .field("binding_sha256", &"<redacted>")
            .finish()
    }
}

#[derive(Clone, Copy, PartialEq, Eq)]
pub struct ExternalModelPartHandleTokens {
    values: [u64; EXTERNAL_MODEL_PART_HANDLE_COUNT],
}

impl ExternalModelPartHandleTokens {
    pub fn try_from_values(
        values: [u64; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    ) -> Result<Self, ExternalModelPartHandleTokenError> {
        validate_values(&values)?;
        Ok(Self { values })
    }

    pub fn try_from_wire_values(
        values: [String; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    ) -> Result<Self, ExternalModelPartHandleTokenError> {
        let mut parsed = [0u64; EXTERNAL_MODEL_PART_HANDLE_COUNT];
        for (index, value) in values.iter().enumerate() {
            parsed[index] = parse_wire_value(value)?;
        }
        Self::try_from_values(parsed)
    }

    #[allow(dead_code)]
    pub fn values(&self) -> [u64; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
        self.values
    }

    pub fn wire_values(&self) -> [String; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
        self.values.map(|value| format!("{value:016x}"))
    }

    /// Validates the six already-inherited handle values in the current
    /// process. The expected commitment is input, never an authentication
    /// source. The returned process-local carrier retains the observed
    /// context/binding; its raw token projection alone is never admission.
    #[cfg(windows)]
    pub fn admit_inherited(
        self,
        generation_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
        expected_external_binding_sha256: [u8; 32],
    ) -> Result<AdmittedExternalModelPartHandles, ExternalModelPartHandleTokenError> {
        if is_zero_digest(&expected_external_binding_sha256) {
            return Err(ExternalModelPartHandleTokenError);
        }
        let context =
            ModelPartHandleBindingContext::try_new(generation_sha256, transaction_sha256)?;
        let before = observe_inherited_external_handles(&self)?;
        let after = observe_inherited_external_handles(&self)?;
        if before != after {
            return Err(ExternalModelPartHandleTokenError);
        }
        let object_identities = before.map(|observation| observation.object_identity_sha256);
        let role_bindings = before.map(|observation| observation.capability_binding_sha256);
        let observed = ExternalModelPartHandleBinding::try_from_handle_bindings(
            context,
            object_identities,
            role_bindings,
        )?;
        if !constant_time_equal(observed.binding_sha256(), &expected_external_binding_sha256) {
            return Err(ExternalModelPartHandleTokenError);
        }
        Ok(AdmittedExternalModelPartHandles {
            tokens: self,
            binding: observed,
        })
    }

    #[cfg(not(windows))]
    pub fn admit_inherited(
        self,
        _generation_sha256: [u8; 32],
        _transaction_sha256: [u8; 32],
        _expected_external_binding_sha256: [u8; 32],
    ) -> Result<AdmittedExternalModelPartHandles, ExternalModelPartHandleTokenError> {
        Err(ExternalModelPartHandleTokenError)
    }

    #[cfg(all(test, windows))]
    pub fn inherited_binding_sha256_for_test(
        &self,
        generation_sha256: [u8; 32],
        transaction_sha256: [u8; 32],
    ) -> Result<[u8; 32], ExternalModelPartHandleTokenError> {
        let context =
            ModelPartHandleBindingContext::try_new(generation_sha256, transaction_sha256)?;
        let observations = observe_inherited_external_handles(self)?;
        ExternalModelPartHandleBinding::try_from_handle_bindings(
            context,
            observations.map(|observation| observation.object_identity_sha256),
            observations.map(|observation| observation.capability_binding_sha256),
        )
        .map(|binding| *binding.binding_sha256())
    }
}

impl fmt::Debug for ExternalModelPartHandleTokens {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_struct("ExternalModelPartHandleTokens")
            .field("roles", &EXTERNAL_MODEL_PART_HANDLE_ROLES)
            .finish_non_exhaustive()
    }
}

impl Serialize for ExternalModelPartHandleTokens {
    fn serialize<S>(&self, serializer: S) -> Result<S::Ok, S::Error>
    where
        S: Serializer,
    {
        let values = self.wire_values();
        let mut sequence = serializer.serialize_seq(Some(EXTERNAL_MODEL_PART_HANDLE_COUNT))?;
        for value in &values {
            sequence.serialize_element(value)?;
        }
        sequence.end()
    }
}

impl<'de> Deserialize<'de> for ExternalModelPartHandleTokens {
    fn deserialize<D>(deserializer: D) -> Result<Self, D::Error>
    where
        D: Deserializer<'de>,
    {
        let values = Vec::<String>::deserialize(deserializer)?;
        let values: [String; EXTERNAL_MODEL_PART_HANDLE_COUNT] =
            values.try_into().map_err(|_| {
                de::Error::custom("external model part handle token count must be exactly six")
            })?;
        Self::try_from_wire_values(values)
            .map_err(|_| de::Error::custom("external model part handle token set is invalid"))
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct ExternalModelPartHandleTokenError;

impl ExternalModelPartHandleTokenError {
    pub fn code(self) -> &'static str {
        "authority_external_model_part_handle_tokens_invalid"
    }
}

impl fmt::Display for ExternalModelPartHandleTokenError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(self.code())
    }
}

impl std::error::Error for ExternalModelPartHandleTokenError {}

fn parse_wire_value(value: &str) -> Result<u64, ExternalModelPartHandleTokenError> {
    if value.len() != HANDLE_TOKEN_HEX_LENGTH
        || !value
            .bytes()
            .all(|byte| byte.is_ascii_digit() || (b'a'..=b'f').contains(&byte))
    {
        return Err(ExternalModelPartHandleTokenError);
    }
    u64::from_str_radix(value, 16).map_err(|_| ExternalModelPartHandleTokenError)
}

fn validate_values(
    values: &[u64; EXTERNAL_MODEL_PART_HANDLE_COUNT],
) -> Result<(), ExternalModelPartHandleTokenError> {
    let invalid_handle_value = usize::MAX as u64;
    for (index, value) in values.iter().enumerate() {
        if *value == 0
            || *value == invalid_handle_value
            || usize::try_from(*value).is_err()
            || values[..index].contains(value)
        {
            return Err(ExternalModelPartHandleTokenError);
        }
    }
    Ok(())
}

#[allow(dead_code)]
fn compose_role_bindings(
    external: &ExternalModelPartHandleBinding,
    protected: &ProtectedModelPartHandleBinding,
) -> Result<
    (
        ModelPartHandleBindingContext,
        [[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT],
        [[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT],
    ),
    ExternalModelPartHandleTokenError,
> {
    if external.context() != protected.context() {
        return Err(ExternalModelPartHandleTokenError);
    }
    let mut fixed_objects = [[0u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT];
    let mut fixed_roles = [[0u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT];
    for (source, target) in EXTERNAL_MODEL_PART_FIXED_INDICES.iter().enumerate() {
        fixed_objects[*target] = external.object_identities()[source];
        fixed_roles[*target] = external.role_bindings()[source];
    }
    for (source, target) in PROTECTED_MODEL_PART_FIXED_INDICES.iter().enumerate() {
        fixed_objects[*target] = protected.object_identities()[source];
        fixed_roles[*target] = protected.role_bindings()[source];
    }
    validate_distinct_nonzero_digests(&fixed_objects)?;
    validate_distinct_nonzero_digests(&fixed_roles)?;
    Ok((external.context(), fixed_objects, fixed_roles))
}

fn external_handle_set_binding_sha256(
    context: &ModelPartHandleBindingContext,
    object_identities: &[[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    role_bindings: &[[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT],
) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(EXTERNAL_HANDLE_SET_BINDING_DOMAIN);
    update_context(&mut hasher, context);
    hasher.update((EXTERNAL_MODEL_PART_HANDLE_COUNT as u64).to_be_bytes());
    for (((role, fixed_index), object_identity), binding) in EXTERNAL_MODEL_PART_HANDLE_ROLES
        .iter()
        .zip(EXTERNAL_MODEL_PART_FIXED_INDICES)
        .zip(object_identities)
        .zip(role_bindings)
    {
        update_role_binding(&mut hasher, fixed_index, role, object_identity, binding);
    }
    hasher.finalize().into()
}

#[allow(dead_code)]
fn fixed_handle_set_binding_sha256(
    context: &ModelPartHandleBindingContext,
    object_identities: &[[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT],
    role_bindings: &[[u8; 32]; FIXED_MODEL_PART_HANDLE_COUNT],
    external_binding_sha256: &[u8; 32],
    protected_binding_sha256: &[u8; 32],
) -> [u8; 32] {
    let mut hasher = Sha256::new();
    hasher.update(FIXED_HANDLE_SET_BINDING_DOMAIN);
    update_context(&mut hasher, context);
    hasher.update(external_binding_sha256);
    hasher.update(protected_binding_sha256);
    hasher.update((FIXED_MODEL_PART_HANDLE_COUNT as u64).to_be_bytes());
    for (index, ((role, object_identity), binding)) in FIXED_MODEL_PART_HANDLE_ROLES
        .iter()
        .zip(object_identities)
        .zip(role_bindings)
        .enumerate()
    {
        update_role_binding(&mut hasher, index, role, object_identity, binding);
    }
    hasher.finalize().into()
}

fn update_context(hasher: &mut Sha256, context: &ModelPartHandleBindingContext) {
    hasher.update(context.generation_sha256());
    hasher.update(context.transaction_sha256());
}

fn update_role_binding(
    hasher: &mut Sha256,
    index: usize,
    role: &str,
    object_identity: &[u8; 32],
    binding: &[u8; 32],
) {
    hasher.update((index as u64).to_be_bytes());
    hasher.update((role.len() as u64).to_be_bytes());
    hasher.update(role.as_bytes());
    hasher.update(object_identity);
    hasher.update(binding);
}

fn validate_distinct_nonzero_digests<const N: usize>(
    values: &[[u8; 32]; N],
) -> Result<(), ExternalModelPartHandleTokenError> {
    for (index, value) in values.iter().enumerate() {
        if is_zero_digest(value) || values[..index].contains(value) {
            return Err(ExternalModelPartHandleTokenError);
        }
    }
    Ok(())
}

fn is_zero_digest(value: &[u8; 32]) -> bool {
    value.iter().all(|byte| *byte == 0)
}

fn constant_time_equal(left: &[u8; 32], right: &[u8; 32]) -> bool {
    left.iter()
        .zip(right)
        .fold(0u8, |difference, (left, right)| difference | (left ^ right))
        == 0
}

#[cfg(windows)]
#[derive(Clone, Copy, PartialEq, Eq)]
struct InheritedExternalHandleObservation {
    object_identity_sha256: [u8; 32],
    capability_binding_sha256: [u8; 32],
}

#[cfg(windows)]
fn observe_inherited_external_handles(
    tokens: &ExternalModelPartHandleTokens,
) -> Result<
    [InheritedExternalHandleObservation; EXTERNAL_MODEL_PART_HANDLE_COUNT],
    ExternalModelPartHandleTokenError,
> {
    let mut observations = Vec::with_capacity(EXTERNAL_MODEL_PART_HANDLE_COUNT);
    for (external_index, value) in tokens.values.iter().copied().enumerate() {
        observations.push(observe_inherited_external_handle(external_index, value)?);
    }
    let observations: [InheritedExternalHandleObservation; EXTERNAL_MODEL_PART_HANDLE_COUNT] =
        observations
            .try_into()
            .map_err(|_| ExternalModelPartHandleTokenError)?;
    validate_distinct_nonzero_digests(
        &observations.map(|observation| observation.object_identity_sha256),
    )?;
    validate_distinct_nonzero_digests(
        &observations.map(|observation| observation.capability_binding_sha256),
    )?;
    Ok(observations)
}

#[cfg(windows)]
#[repr(C)]
#[derive(Clone, Copy)]
struct HandleObjectBasicInformation {
    attributes: u32,
    granted_access: u32,
    handle_count: u32,
    pointer_count: u32,
    reserved: [u32; 10],
}

#[cfg(windows)]
#[derive(Clone, Copy, PartialEq, Eq)]
struct StableInheritedFileIdentity {
    volume_serial_number: u32,
    file_index: u64,
    size: u64,
    creation_time: u64,
    last_write_time: u64,
    link_count: u32,
    attributes: u32,
}

#[cfg(windows)]
fn external_handle_access_is_exact_read_only(granted_access: u32) -> bool {
    use windows_sys::Win32::Storage::FileSystem::{
        FILE_READ_ATTRIBUTES, FILE_READ_DATA, FILE_READ_EA, READ_CONTROL, SYNCHRONIZE,
    };

    const EXACT_READ_ONLY_ACCESS: u32 =
        FILE_READ_DATA | FILE_READ_EA | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE;
    granted_access & FILE_READ_DATA != 0 && granted_access & !EXACT_READ_ONLY_ACCESS == 0
}

#[cfg(windows)]
fn observe_inherited_external_handle(
    external_index: usize,
    value: u64,
) -> Result<InheritedExternalHandleObservation, ExternalModelPartHandleTokenError> {
    use std::mem::{size_of, zeroed};
    use windows_sys::Wdk::Foundation::{NtQueryObject, ObjectBasicInformation};
    use windows_sys::Win32::{
        Foundation::{GetHandleInformation, HANDLE_FLAG_INHERIT},
        Storage::FileSystem::{
            GetFileInformationByHandle, GetFileType, BY_HANDLE_FILE_INFORMATION,
            FILE_ATTRIBUTE_DIRECTORY, FILE_ATTRIBUTE_REPARSE_POINT, FILE_TYPE_DISK,
        },
    };

    if external_index >= EXTERNAL_MODEL_PART_HANDLE_COUNT {
        return Err(ExternalModelPartHandleTokenError);
    }
    let raw = value as usize as windows_sys::Win32::Foundation::HANDLE;
    if unsafe { GetFileType(raw) } != FILE_TYPE_DISK {
        return Err(ExternalModelPartHandleTokenError);
    }
    let mut flags = 0u32;
    if unsafe { GetHandleInformation(raw, &mut flags) } == 0 || flags != HANDLE_FLAG_INHERIT {
        return Err(ExternalModelPartHandleTokenError);
    }

    let mut object = unsafe { zeroed::<HandleObjectBasicInformation>() };
    let mut returned = 0u32;
    let status = unsafe {
        NtQueryObject(
            raw,
            ObjectBasicInformation,
            (&mut object as *mut HandleObjectBasicInformation).cast(),
            size_of::<HandleObjectBasicInformation>() as u32,
            &mut returned,
        )
    };
    if status < 0 || returned < (size_of::<u32>() * 2) as u32 {
        return Err(ExternalModelPartHandleTokenError);
    }
    if !external_handle_access_is_exact_read_only(object.granted_access) {
        return Err(ExternalModelPartHandleTokenError);
    }

    let mut information = unsafe { zeroed::<BY_HANDLE_FILE_INFORMATION>() };
    if unsafe { GetFileInformationByHandle(raw, &mut information) } == 0
        || information.dwFileAttributes & (FILE_ATTRIBUTE_DIRECTORY | FILE_ATTRIBUTE_REPARSE_POINT)
            != 0
        || information.nNumberOfLinks != 1
    {
        return Err(ExternalModelPartHandleTokenError);
    }
    let identity = StableInheritedFileIdentity {
        volume_serial_number: information.dwVolumeSerialNumber,
        file_index: ((information.nFileIndexHigh as u64) << 32) | information.nFileIndexLow as u64,
        size: ((information.nFileSizeHigh as u64) << 32) | information.nFileSizeLow as u64,
        creation_time: ((information.ftCreationTime.dwHighDateTime as u64) << 32)
            | information.ftCreationTime.dwLowDateTime as u64,
        last_write_time: ((information.ftLastWriteTime.dwHighDateTime as u64) << 32)
            | information.ftLastWriteTime.dwLowDateTime as u64,
        link_count: information.nNumberOfLinks,
        attributes: information.dwFileAttributes,
    };
    if identity.file_index == 0 || identity.size == 0 {
        return Err(ExternalModelPartHandleTokenError);
    }

    let mut object_hasher = Sha256::new();
    object_hasher.update(EXTERNAL_HANDLE_OBJECT_IDENTITY_DOMAIN);
    object_hasher.update(identity.volume_serial_number.to_be_bytes());
    object_hasher.update(identity.file_index.to_be_bytes());
    object_hasher.update(identity.size.to_be_bytes());
    object_hasher.update(identity.creation_time.to_be_bytes());
    object_hasher.update(identity.last_write_time.to_be_bytes());
    object_hasher.update(identity.link_count.to_be_bytes());
    object_hasher.update(identity.attributes.to_be_bytes());
    let object_identity_sha256: [u8; 32] = object_hasher.finalize().into();

    let mut capability_hasher = Sha256::new();
    capability_hasher.update(EXTERNAL_HANDLE_CAPABILITY_BINDING_DOMAIN);
    capability_hasher.update(object_identity_sha256);
    capability_hasher.update(object.granted_access.to_be_bytes());
    capability_hasher.update(flags.to_be_bytes());
    capability_hasher.update(FILE_TYPE_DISK.to_be_bytes());
    Ok(InheritedExternalHandleObservation {
        object_identity_sha256,
        capability_binding_sha256: capability_hasher.finalize().into(),
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    fn values() -> [u64; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
        [0x11, 0x22, 0x33, 0x44, 0x55, 0x66]
    }

    fn context() -> ModelPartHandleBindingContext {
        ModelPartHandleBindingContext::try_new([0x71; 32], [0x72; 32]).unwrap()
    }

    fn external_bindings() -> [[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
        std::array::from_fn(|index| [0x10 + index as u8; 32])
    }

    fn external_object_identities() -> [[u8; 32]; EXTERNAL_MODEL_PART_HANDLE_COUNT] {
        std::array::from_fn(|index| [0x30 + index as u8; 32])
    }

    fn protected_bindings() -> [[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT] {
        [[0x21; 32], [0x22; 32]]
    }

    fn protected_object_identities() -> [[u8; 32]; PROTECTED_MODEL_PART_HANDLE_COUNT] {
        [[0x41; 32], [0x42; 32]]
    }

    #[test]
    fn external_model_part_handle_tokens_require_exact_six_canonical_values() {
        assert_eq!(
            EXTERNAL_MODEL_PART_HANDLE_ROLES,
            [
                "desktop",
                "backend",
                "unity",
                "bridge_listener",
                "fixture_contract",
                "fixture_baseline",
            ]
        );
        let tokens = ExternalModelPartHandleTokens::try_from_values(values()).unwrap();
        let wire = tokens.wire_values();
        assert_eq!(wire[0], "0000000000000011");
        assert_eq!(wire[5], "0000000000000066");
        assert_eq!(
            ExternalModelPartHandleTokens::try_from_wire_values(wire).unwrap(),
            tokens
        );
        assert_eq!(
            serde_json::to_value(tokens).unwrap(),
            serde_json::json!([
                "0000000000000011",
                "0000000000000022",
                "0000000000000033",
                "0000000000000044",
                "0000000000000055",
                "0000000000000066",
            ])
        );

        for invalid in [
            serde_json::json!([]),
            serde_json::Value::Array(vec![
                serde_json::Value::String(
                    "0000000000000011".to_string()
                );
                5
            ]),
            serde_json::Value::Array(vec![
                serde_json::Value::String(
                    "0000000000000011".to_string()
                );
                7
            ]),
            serde_json::Value::Array(vec![
                serde_json::Value::String(
                    "0000000000000011".to_string()
                );
                8
            ]),
        ] {
            assert!(serde_json::from_value::<ExternalModelPartHandleTokens>(invalid).is_err());
        }
    }

    #[test]
    fn external_model_part_handle_tokens_reject_zero_invalid_duplicates_and_uppercase() {
        let mut zero = values();
        zero[3] = 0;
        assert!(ExternalModelPartHandleTokens::try_from_values(zero).is_err());

        let mut invalid = values();
        invalid[3] = usize::MAX as u64;
        assert!(ExternalModelPartHandleTokens::try_from_values(invalid).is_err());

        let mut duplicate = values();
        duplicate[5] = duplicate[0];
        assert!(ExternalModelPartHandleTokens::try_from_values(duplicate).is_err());

        let uppercase = [
            "0000000000000011".to_string(),
            "0000000000000022".to_string(),
            "0000000000000033".to_string(),
            "0000000000000044".to_string(),
            "0000000000000055".to_string(),
            "00000000000000AA".to_string(),
        ];
        assert!(ExternalModelPartHandleTokens::try_from_wire_values(uppercase).is_err());

        let mut invalid_wire = values().map(|value| format!("{value:016x}"));
        invalid_wire[5] = "66".to_string();
        assert!(ExternalModelPartHandleTokens::try_from_wire_values(invalid_wire).is_err());
    }

    #[test]
    fn external_model_part_handle_tokens_debug_does_not_disclose_capabilities() {
        let tokens = ExternalModelPartHandleTokens::try_from_values(values()).unwrap();
        let debug = format!("{tokens:?}");
        assert!(!debug.contains("0000000000000011"));
        assert!(!debug.contains("136"));
    }

    #[cfg(windows)]
    #[test]
    fn external_handle_access_is_an_exact_read_only_allowlist() {
        use windows_sys::Win32::{
            Storage::FileSystem::{
                FILE_APPEND_DATA, FILE_DELETE_CHILD, FILE_READ_ATTRIBUTES, FILE_READ_DATA,
                FILE_READ_EA, FILE_WRITE_ATTRIBUTES, FILE_WRITE_DATA, FILE_WRITE_EA, READ_CONTROL,
                SYNCHRONIZE, WRITE_DAC, WRITE_OWNER,
            },
            System::SystemServices::ACCESS_SYSTEM_SECURITY,
        };

        let exact_read_only =
            FILE_READ_DATA | FILE_READ_EA | FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE;
        assert!(external_handle_access_is_exact_read_only(exact_read_only));
        assert!(external_handle_access_is_exact_read_only(FILE_READ_DATA));
        for excluded in [
            FILE_WRITE_DATA,
            FILE_APPEND_DATA,
            FILE_WRITE_EA,
            FILE_DELETE_CHILD,
            FILE_WRITE_ATTRIBUTES,
            WRITE_DAC,
            WRITE_OWNER,
            ACCESS_SYSTEM_SECURITY,
            0x8000_0000,
        ] {
            assert!(!external_handle_access_is_exact_read_only(
                exact_read_only | excluded
            ));
        }
        assert!(!external_handle_access_is_exact_read_only(
            FILE_READ_ATTRIBUTES | READ_CONTROL | SYNCHRONIZE
        ));
    }

    #[test]
    fn fixed_eight_roles_are_exactly_external_six_plus_protected_two() {
        assert_eq!(
            FIXED_MODEL_PART_HANDLE_ROLES,
            [
                "driver",
                "desktop",
                "backend",
                "unity",
                "bridge_launcher",
                "bridge_listener",
                "fixture_contract",
                "fixture_baseline",
            ]
        );
        assert_eq!(EXTERNAL_MODEL_PART_FIXED_INDICES, [1, 2, 3, 5, 6, 7]);
        assert_eq!(PROTECTED_MODEL_PART_FIXED_INDICES, [0, 4]);
        assert!(!EXTERNAL_MODEL_PART_HANDLE_ROLES.contains(&"driver"));
        assert!(!EXTERNAL_MODEL_PART_HANDLE_ROLES.contains(&"bridge_launcher"));
        let mut covered = EXTERNAL_MODEL_PART_FIXED_INDICES.to_vec();
        covered.extend(PROTECTED_MODEL_PART_FIXED_INDICES);
        covered.sort_unstable();
        assert_eq!(
            covered,
            (0..FIXED_MODEL_PART_HANDLE_COUNT).collect::<Vec<_>>()
        );
    }

    #[test]
    fn fixed_eight_composition_requires_context_order_and_expected_binding() {
        let external = ExternalModelPartHandleBinding::try_from_handle_bindings(
            context(),
            external_object_identities(),
            external_bindings(),
        )
        .unwrap();
        let protected = ProtectedModelPartHandleBinding::try_from_handle_bindings(
            context(),
            protected_object_identities(),
            protected_bindings(),
        )
        .unwrap();
        let expected =
            FixedModelPartHandleComposition::expected_binding_sha256(&external, &protected)
                .unwrap();
        let composition =
            FixedModelPartHandleComposition::compose(external, protected, expected).unwrap();
        assert_eq!(composition.context(), context());
        assert_eq!(composition.binding_sha256(), &expected);
        assert_eq!(composition.object_identities()[0], [0x41; 32]);
        assert_eq!(composition.object_identities()[1], [0x30; 32]);
        assert_eq!(composition.object_identities()[4], [0x42; 32]);
        assert_eq!(composition.object_identities()[7], [0x35; 32]);
        assert_eq!(composition.role_bindings()[0], [0x21; 32]);
        assert_eq!(composition.role_bindings()[1], [0x10; 32]);
        assert_eq!(composition.role_bindings()[4], [0x22; 32]);
        assert_eq!(composition.role_bindings()[7], [0x15; 32]);

        let mut reordered = external_bindings();
        reordered.swap(0, 1);
        let reordered = ExternalModelPartHandleBinding::try_from_handle_bindings(
            context(),
            external_object_identities(),
            reordered,
        )
        .unwrap();
        assert!(FixedModelPartHandleComposition::compose(reordered, protected, expected).is_err());

        let mut reordered_objects = external_object_identities();
        reordered_objects.swap(4, 5);
        let reordered_objects = ExternalModelPartHandleBinding::try_from_handle_bindings(
            context(),
            reordered_objects,
            external_bindings(),
        )
        .unwrap();
        assert!(
            FixedModelPartHandleComposition::compose(reordered_objects, protected, expected)
                .is_err()
        );

        let drifted_context =
            ModelPartHandleBindingContext::try_new([0x71; 32], [0x73; 32]).unwrap();
        let drifted = ProtectedModelPartHandleBinding::try_from_handle_bindings(
            drifted_context,
            protected_object_identities(),
            protected_bindings(),
        )
        .unwrap();
        assert!(FixedModelPartHandleComposition::compose(external, drifted, expected).is_err());
    }

    #[test]
    fn fixed_eight_composition_rejects_cross_set_alias_and_invalid_context() {
        let external = ExternalModelPartHandleBinding::try_from_handle_bindings(
            context(),
            external_object_identities(),
            external_bindings(),
        )
        .unwrap();
        let mut protected_objects = protected_object_identities();
        protected_objects[1] = external_object_identities()[3];
        let protected = ProtectedModelPartHandleBinding::try_from_handle_bindings(
            context(),
            protected_objects,
            protected_bindings(),
        )
        .unwrap();
        assert!(
            FixedModelPartHandleComposition::expected_binding_sha256(&external, &protected)
                .is_err()
        );
        assert!(ModelPartHandleBindingContext::try_new([0; 32], [0x72; 32]).is_err());
        assert!(ModelPartHandleBindingContext::try_new([0x71; 32], [0x71; 32]).is_err());
    }
}
