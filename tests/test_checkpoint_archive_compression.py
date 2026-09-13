import hashlib
import zipfile

from agent_gateway import AgentGateway


def test_archive_checkpoint_stores_compressed_asset_and_restores_bytes(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "Project"
    png = project / "Assets" / "Matcap.PNG"
    plain = project / "Assets" / "settings.asset"
    png_bytes = bytes(range(256)) * 4096
    plain_bytes = b"plain checkpoint data\x00\xff"
    png.parent.mkdir(parents=True)
    png.write_bytes(png_bytes)
    plain.write_bytes(plain_bytes)

    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(
        project, {"id": "ckpt_compression", "projectRoot": str(project), "status": "unavailable"}
    )
    assert checkpoint["ok"] is True
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert archive.testzip() is None
        png_info = archive.getinfo("Assets/Matcap.PNG")
        plain_info = archive.getinfo("Assets/settings.asset")
        assert png_info.compress_type == zipfile.ZIP_STORED
        assert plain_info.compress_type == zipfile.ZIP_DEFLATED
        restored = archive.read(png_info)
        assert hashlib.sha256(restored).hexdigest() == hashlib.sha256(png_bytes).hexdigest()
        assert archive.read(plain_info) == plain_bytes


def test_archive_checkpoint_keeps_level1_deflate_for_uncompressed_extensions(tmp_path):
    gateway = AgentGateway(tmp_path / "config.json", tmp_path / "audit")
    project = tmp_path / "Project"
    source = project / "Assets" / "scene.asset"
    source.parent.mkdir(parents=True)
    source.write_bytes(b"scene data" * 1024)

    checkpoint = gateway.checkpoint_recovery._create_archive_checkpoint(
        project, {"id": "ckpt_plain_compression", "projectRoot": str(project), "status": "unavailable"}
    )
    with zipfile.ZipFile(checkpoint["archivePath"]) as archive:
        assert archive.getinfo("Assets/scene.asset").compress_type == zipfile.ZIP_DEFLATED
