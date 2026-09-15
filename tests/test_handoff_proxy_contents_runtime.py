"""Actual Capture eligibility section executes for both finalize and abort before mutation."""
from pathlib import Path
import os,subprocess
from test_constraint_conversion_scope_runtime import run
ROOT=Path(__file__).resolve().parents[1]
def test_handoff_capture_rejects_extra_proxy_content(tmp_path):
    path='Assets/VRCForge/Editor/UserAdjustmentHandoffTool.cs';ref=os.environ.get('VRCFORGE_HANDOFF_CONTENT_GIT_REF')
    raw=subprocess.check_output(['git','show',f'{ref}:{path}'],cwd=ROOT,text=True,encoding='utf-8') if ref else (ROOT/path).read_text(encoding='utf-8-sig')
    start=raw.index('                proxy = Resolve(state[');end=raw.index('            var scenePath =',start)
    block=raw[start:end].rstrip();block=block[:block.rfind('}')]
    run(tmp_path,r'''using System;using System.Linq;using System.Collections.Generic;
class Component{} class Transform:Component{public Transform parent;public List<Transform> children=new List<Transform>();public int childCount=>children.Count;public Transform GetChild(int i)=>children[i];}
class GameObject{public string name="proxy";public Transform transform=new Transform();public List<Component> components=new List<Component>();public T[] GetComponents<T>()=>components.Cast<T>().ToArray();}
class Probe{static GameObject owned;static GameObject Resolve(string id,string label)=>owned;
static void CaptureEligibility(GameObject target){GameObject proxy=null;var state=new Dictionary<string,string>{{"proxyGlobalObjectId","proxy-id"},{"proxyName","proxy"}};
BLOCK
}
static bool Reject(GameObject t){try{CaptureEligibility(t);return false;}catch(InvalidOperationException){return true;}}
static void Check(bool ok,string msg){if(!ok)throw new Exception(msg);}
static void Main(){owned=new GameObject();owned.components.Add(owned.transform);var target=new GameObject();target.transform.parent=owned.transform;owned.transform.children.Add(target.transform);Check(!Reject(target),"normal proxy rejected");
owned.transform.children.Add(new Transform());Check(Reject(target),"extra user child accepted for destruction");owned.transform.children.RemoveAt(1);
owned.components.Add(new Component());Check(Reject(target),"extra user component accepted for destruction");owned.components.RemoveAt(1);
owned.components.Add(null);Check(Reject(target),"missing script accepted for destruction");owned.components.RemoveAt(1);Check(!Reject(target),"restored normal proxy rejected");}}
'''.replace('BLOCK',block))
