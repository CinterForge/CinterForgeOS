import io
import json
import zipfile
from pathlib import Path

from cf.build_cinterforge.craftmaster import craftmaster_serve as serve
def _pack(module_type, pack_id):
    stream=io.BytesIO()
    with zipfile.ZipFile(stream,'w') as z:
        z.writestr('manifest.json',json.dumps({'format_version':2,'header':{'name':'Test','description':'Test','uuid':pack_id,'version':[1,0,0],'min_engine_version':[1,20,0]},'modules':[{'type':module_type,'uuid':pack_id[:-1]+'f','version':[1,0,0]}]}))
        z.writestr('payload.txt',module_type)
    return stream.getvalue()


def test_install_mcaddon_binds_both_packs_without_touching_source(tmp_path):
    server=tmp_path/'Server'; world=server/'worlds'/'Small World'; world.mkdir(parents=True)
    (world/'level.dat').write_bytes(b'world')
    addon=tmp_path/'DefaultCharacter.mcaddon'
    resource_id='11111111-1111-1111-1111-111111111111'
    behavior_id='22222222-2222-2222-2222-222222222222'
    with zipfile.ZipFile(addon,'w') as z:
        z.writestr('default_RP.mcpack',_pack('resources',resource_id))
        z.writestr('default_BP.mcpack',_pack('data',behavior_id))
    before=addon.read_bytes()
    result=serve.install_mcaddon(addon,server,'Small World')
    assert addon.read_bytes()==before
    assert (server/'resource_packs'/f'cinterforge_{resource_id}'/'payload.txt').read_text()=='resources'
    assert (server/'behavior_packs'/f'cinterforge_{behavior_id}'/'payload.txt').read_text()=='data'
    assert json.loads((world/'world_resource_packs.json').read_text())==[{'pack_id':resource_id,'version':[1,0,0]}]
    assert json.loads((world/'world_behavior_packs.json').read_text())==[{'pack_id':behavior_id,'version':[1,0,0]}]
    assert len(result['resource_packs'])==1 and len(result['behavior_packs'])==1


def test_install_mcaddon_preserves_unrelated_world_pack_references(tmp_path):
    server=tmp_path/'Server'; world=server/'worlds'/'Small World'; world.mkdir(parents=True)
    (world/'level.dat').write_bytes(b'world')
    existing={'pack_id':'aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa','version':[9,9,9]}
    (world/'world_behavior_packs.json').write_text(json.dumps([existing]))
    addon=tmp_path/'OnlyBehavior.mcaddon'; behavior_id='22222222-2222-2222-2222-222222222222'
    with zipfile.ZipFile(addon,'w') as z: z.writestr('default_BP.mcpack',_pack('data',behavior_id))
    serve.install_mcaddon(addon,server,'Small World')
    refs=json.loads((world/'world_behavior_packs.json').read_text())
    assert existing in refs
    assert {'pack_id':behavior_id,'version':[1,0,0]} in refs


def test_install_cinter_utility_pack_builds_chests_from_world_cinter_items(tmp_path):
    server = tmp_path / 'Server'
    world = server / 'worlds' / 'Small World'
    world.mkdir(parents=True)
    behavior_id = '22222222-2222-2222-2222-222222222222'
    pack = server / 'behavior_packs' / f'cinterforge_{behavior_id}'
    (pack / 'items').mkdir(parents=True)
    (pack / 'manifest.json').write_text(json.dumps({
        'format_version': 2,
        'header': {'uuid': behavior_id, 'version': [1, 0, 0]},
        'modules': [{'type': 'data', 'uuid': '33333333-3333-3333-3333-333333333333', 'version': [1, 0, 0]}],
    }))
    (pack / 'items' / 'defaultcharacter.item.json').write_text(json.dumps({
        'minecraft:item': {'description': {'identifier': 'cinter:defaultcharacter'}}
    }))
    (pack / 'items' / 'foreign.item.json').write_text(json.dumps({
        'minecraft:item': {'description': {'identifier': 'other:not_cinter'}}
    }))
    (world / 'world_behavior_packs.json').write_text(json.dumps([{'pack_id': behavior_id, 'version': [1, 0, 0]}]))

    result = serve.install_cinter_utility_pack(server, 'Small World')

    assert result['items'] == ['cinter:defaultcharacter']
    function_path = Path(result['pack']) / 'functions' / 'cinter' / 'makechests.mcfunction'
    assert function_path.read_text() == (
        'setblock ^0 ^ ^3 chest replace\n'
        'replaceitem block ^0 ^ ^3 slot.container 0 cinter:defaultcharacter 1\n'
    )
    assert not (Path(result['pack']) / 'functions' / 'cinter' / 'give_all.mcfunction').exists()
    assert result['command'] == '/function cinter/makechests'
    refs = json.loads((world / 'world_behavior_packs.json').read_text())
    assert {'pack_id': serve._CINTER_UTILITY_PACK_ID, 'version': [1, 0, 0]} in refs
