"""Read-only scope diagnostics are opt-in and cannot redefine validity."""
from pathlib import Path


SOURCE = Path(__file__).resolve().parents[1] / "Assets/VRCForge/Editor/MCP/ScanScopeJournal.cs"


def test_scope_object_diagnostics_are_opt_in_and_keep_existing_comparison():
    source = SOURCE.read_text(encoding="utf-8-sig")
    assert 'public bool includeDiagnostics { get; set; }' in source
    assert 'parameters?["includeDiagnostics"]' in source
    assert 'result["objectDiagnostics"]' in source
    assert 'saved.objects.All(pair => current.objects.TryGetValue(pair.Key, out var value) && value == pair.Value)' in source
    assert 'if (saved.epoch != state.epoch) return Receipt(false, "domain_reload_observation_gap");' in source


def test_scope_diagnostics_expose_separate_content_dirty_and_revision():
    source = SOURCE.read_text(encoding="utf-8-sig")
    for field in ('"contentHash"', '"dirtyCount"', '"revision"', '"old"', '"new"'):
        assert field in source
    for classification in ('"content_changed"', '"dirty_count_only"', '"revision_only"', '"unchanged"'):
        assert classification in source
    assert 'Take(MaxObjects * 2)' in source
