"""Production rollback services, ordinary files and actual Windows sharing denial."""
import base64
import hashlib
import json
import os
from contextlib import nullcontext

import pytest

from project_lifecycle_service import ProjectLifecycleService, PROJECT_LIFECYCLE_RECEIPT_SCHEMA, _canonical_json_bytes
from project_catalog_registration_service import ProjectCatalogRegistrationService, PROJECT_CATALOG_RECEIPT_SCHEMA
from test_checkpoint_archive_atomic_restore import deny_replace


@pytest.mark.parametrize('kind', ['lifecycle', 'catalog'])
@pytest.mark.parametrize('locked', [True, False])
def test_rollback_receipt_failure_preserves_current_catalog_and_retry(tmp_path, kind, locked):
    if locked and os.name != 'nt':
        pytest.skip('Requires Windows delete-sharing denial')
    receipts = tmp_path / 'receipts'
    receipts.mkdir()
    target = tmp_path / 'settings.json'
    rid = 'receipt_failure_123456'
    before = {'version': 2, 'customProjects': [], 'hiddenPaths': []}
    after = dict(before, customProjects=[{'path': 'retained-entry'}])
    before_bytes = json.dumps(before).encode()
    after_bytes = json.dumps(after).encode()
    target.write_bytes(after_bytes)
    receipt = {'receiptId': rid, 'status': 'active', 'kind': 'register_project'}
    if kind == 'lifecycle':
        service = ProjectLifecycleService(prefs_path=target, receipts_dir=receipts, template_roots=[])
        receipt.update(schema=PROJECT_LIFECYCLE_RECEIPT_SCHEMA, prefsBefore=before,
                       prefsAfterSha256=hashlib.sha256(_canonical_json_bytes(after)).hexdigest())
    else:
        service = ProjectCatalogRegistrationService(receipts_dir=receipts, catalog_paths={'vcc': [target]})
        receipt.update(schema=PROJECT_CATALOG_RECEIPT_SCHEMA, catalog='vcc', settingsPath=str(target),
                       settingsBeforeBase64=base64.b64encode(before_bytes).decode(),
                       settingsBeforeSha256=hashlib.sha256(before_bytes).hexdigest(),
                       settingsAfterSha256=hashlib.sha256(after_bytes).hexdigest())
    receipt_path = receipts / f'{rid}.json'
    receipt_path.write_text(json.dumps(receipt), encoding='utf-8')
    original_receipt = receipt_path.read_bytes()
    with deny_replace(receipt_path) if locked else nullcontext():
        if locked:
            with pytest.raises((OSError, RuntimeError)):
                service.rollback({'receiptId': rid})
            assert json.loads(target.read_bytes()) == after
            assert receipt_path.read_bytes() == original_receipt
        else:
            assert service.rollback({'receiptId': rid})['committed']
    if locked:
        assert service.rollback({'receiptId': rid})['committed']
    assert json.loads(target.read_bytes()) == before
    assert json.loads(receipt_path.read_bytes())['status'] == 'rolled_back'
    assert not list(tmp_path.rglob('*.tmp'))
