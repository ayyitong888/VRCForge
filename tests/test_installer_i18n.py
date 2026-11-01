"""Public installer messages must exist in each shipped NSIS language."""
from pathlib import Path
import re


ROOT = Path(__file__).resolve().parents[1] / "installer"
LANGUAGES = {"SIMPCHINESE", "TRADCHINESE", "JAPANESE", "ENGLISH"}


def test_installer_and_uninstaller_messages_cover_all_shipped_languages():
    shared = (ROOT / "VRCForge_InstallFinalization.nsh").read_text(encoding="utf-8-sig")
    for name in ("VRCForge_Offline_Installer_x64.nsi", "VRCForge_Web_Installer_x64.nsi"):
        source = (ROOT / name).read_text(encoding="utf-8-sig")
        translations = {}
        for key, language in re.findall(r"^LangString\s+(\w+)\s+\$\{LANG_(\w+)\}", source + "\n" + shared, re.M):
            translations.setdefault(key, set()).add(language)
        assert translations
        assert {key: languages for key, languages in translations.items() if languages != LANGUAGES} == {}
        for message in re.findall(r'\bMessageBox\s+[^"\n]*"([^"\n]*)"', source + "\n" + shared):
            assert message.startswith("$("), f"{name} has an untranslated primary message: {message}"
