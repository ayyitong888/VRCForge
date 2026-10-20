"""Texture folder import is independent from later material-slot assignment."""
import zipfile
from pathlib import Path

from outfit_import_planner import build_outfit_import_plan
from test_outfit_import_planner import make_project


def test_texture_folder_is_importable_without_a_prefab_or_material_assignment(tmp_path):
    project=tmp_path/'Project'
    make_project(project)
    source=tmp_path/'Textures'
    source.mkdir()
    (source/'Body.png').write_bytes(b'body source')
    (source/'Face.png').write_bytes(b'face source')
    result=build_outfit_import_plan(source,project_path=project,target_folder='Assets/Textures')
    plan=result['plan']
    assert result['ok'] and plan['readyToApply']
    assert plan['kind']=='loose_prefab_copy'
    assert plan['selectedPrefab']==''
    assert sorted(plan['expectedAssetPaths'])==['Assets/Textures/Body.png','Assets/Textures/Face.png']
    assert plan['requiresApproval'] and plan['requiresCheckpoint']
    assert not (project/'Assets/Textures').exists()


def test_texture_zip_and_single_image_still_need_an_unpacked_folder(tmp_path):
    project=tmp_path/'Project'
    make_project(project)
    source=tmp_path/'Textures.zip'
    with zipfile.ZipFile(source,'w') as archive:
        archive.writestr('Body.png',b'body source')
    image=tmp_path/'Body.png'
    image.write_bytes(b'body source')
    for path in (source,image):
        result=build_outfit_import_plan(path,project_path=project)
        assert not result['plan']['readyToApply']
    assert list((project/'Assets').iterdir())==[]


def test_category_truncated_texture_listing_cannot_offer_a_complete_copy_plan(tmp_path):
    project=tmp_path/'Project'
    make_project(project)
    source=tmp_path/'Textures'
    source.mkdir()
    for index in range(201):
        (source/f'body{index}.png').write_bytes(b'source')
    result=build_outfit_import_plan(source,project_path=project)
    assert not result['plan']['readyToApply']
