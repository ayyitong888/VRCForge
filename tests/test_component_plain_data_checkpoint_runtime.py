"""Compile the production data-field gate with explicit Unity identity seams.

These probes cover field admission and checkpoint ownership; real Unity
serialization, scene reopen and callback behavior require the live Core gate.
"""
from test_curve_fx_authoring_runtime_contract import method
from test_component_property_checkpoint_runtime import run, source
from unity_component_checkpoint import prepare_component_property_checkpoint


def test_real_core_scope_selects_project_for_behavior_without_invoking_getters(tmp_path):
    raw = source()
    signatures = ["private static bool IsEngineComponentAssembly("]
    for signature in ("private static bool IsPlainSerializedFieldType(Type type, bool allowCollection = true)",
                      "private static bool IsPlainSerializedFieldType(Type type, bool allowCollection, HashSet<Type> visiting)",
                      "private static bool IsPlainSceneDataField("):
        if signature in raw:
            signatures.append(signature)
    signatures += ["internal static object BuildPropertyCheckpointScope(", "internal static object GetMemberValue(", "internal static void SetMemberValue("]
    helpers = "\n".join(method(raw, s) for s in signatures)
    program = r'''
using System;using System.Linq;using System.Collections.Generic;using System.Reflection;using Newtonsoft.Json.Linq;using UnityEngine;using UnityEditor;
namespace UnityEngine {
 public class Object {} public class Material:Object {} public struct Scene{public string path;public int handle;}
 public class GameObject:Object {public Scene scene;public string id="object";}
 public class Component:Object {public GameObject gameObject;public string id="component";public int native;}
 public class MonoBehaviour:Component {}
 public interface ISerializationCallbackReceiver {void OnBeforeSerialize();void OnAfterDeserialize();}
 public class SerializeField:Attribute {} public class SerializeReference:Attribute {}
 [AttributeUsage(AttributeTargets.Class,Inherited=true)] public class ExecuteAlways:Attribute {}
 [AttributeUsage(AttributeTargets.Class,Inherited=true)] public class ExecuteInEditMode:Attribute {}
 public struct Vector2{} public struct Vector3{} public struct Vector4{} public struct Quaternion{} public struct Color{}
}
namespace UnityEditor {
 public struct GuidValue{public string value;public override string ToString()=>value;}
 public struct GlobalObjectId {
  public int identifierType;public ulong targetObjectId;public GuidValue assetGUID;public string value;
  public static string guid="guid";
  public static GlobalObjectId GetGlobalObjectIdSlow(object o)=>new GlobalObjectId{identifierType=2,targetObjectId=1,assetGUID=new GuidValue{value=guid},value=o is Component c?c.id:((GameObject)o).id};
  public override string ToString()=>value;
 }
}
class SavedSceneSnapshot{public string Path="Assets/Main.unity",Guid="guid",FileDigest="before";public int Handle=17;}
class Plain:MonoBehaviour {public int number;public Vector3 vector;public GameObject reference;public int[] numbers;public List<GameObject> refs;[SerializeField]private float hidden;[NonSerialized]public int transient;public readonly int readOnly;public static int shared;}
class Managed:MonoBehaviour{public int number;[SerializeReference]public object managed;}
class DecimalData:MonoBehaviour{public int number;public decimal value;}
class Nested:MonoBehaviour{public int number;public int[][] value;}
class Matrix:MonoBehaviour{public int number;public int[,] value;}
class CustomData:MonoBehaviour{public int number;public Custom value;}
class CustomCollection:MonoBehaviour{public int number;public CustomList value;}
class Custom{public int x;} class CustomList:List<int>{}
class InheritedPlain:Plain {public bool flag;}
class PropertyBehavior:MonoBehaviour {public static int calls;public int value {get{calls++;return 0;}set{calls++;}}public int field;}
class CallbackBase:MonoBehaviour {private void OnValidate(){} public int number;}
class CallbackChild:CallbackBase{}
[Serializable]class AvatarObjectReference {public string referencePath;[SerializeField]internal GameObject targetObject;}
[Serializable]class MaterialSwitchObject {public AvatarObjectReference Object;public Material Material;public int MaterialIndex;}
class MaterialSetter:CallbackBase {[SerializeField]private List<MaterialSwitchObject> m_objects;public static int reads;public List<MaterialSwitchObject> Objects {get{reads++;return m_objects;}set{reads++;m_objects=value;}}}
class Serialization:MonoBehaviour,ISerializationCallbackReceiver {public int number;public void OnBeforeSerialize(){}public void OnAfterDeserialize(){}}
[ExecuteAlways]class Executing:MonoBehaviour{public int number;}
class ExecutingChild:Executing{}
[ExecuteInEditMode]class Editing:MonoBehaviour{public int number;}
class HelperBehavior:MonoBehaviour{public int number;public void Helper(){}}
class StaticBehavior:MonoBehaviour{static StaticBehavior(){}public int number;}
class Probe {
 HELPERS
 static void Check(bool b,string m){if(!b)throw new Exception(m);}
 static SavedSceneSnapshot scene=new SavedSceneSnapshot();
 static GameObject go=new GameObject{scene=new Scene{path="Assets/Main.unity",handle=17}};
 static MemberInfo Member(Type t,string name)=>t.GetField(name,BindingFlags.Instance|BindingFlags.Static|BindingFlags.Public|BindingFlags.NonPublic) as MemberInfo??t.GetProperty(name);
 static void Accept(Component c,string name,string archiveScope="scene"){c.gameObject=go;var scope=JObject.FromObject(BuildPropertyCheckpointScope(c,Member(c.GetType(),name),0,name,scene));Check((string)scope["scenePath"]==scene.Path&&(string)scope["componentGlobalObjectId"]=="component"&&scope["archiveAssetPaths"].ToString()==new JArray(scene.Path).ToString(),"scope");Check((string)scope["archiveScope"]==archiveScope,"archive scope for "+c.GetType().Name);}
 static void Reject(Component c,string name){c.gameObject=go;try{BuildPropertyCheckpointScope(c,Member(c.GetType(),name),0,name,scene);}catch(InvalidOperationException){return;}throw new Exception("accepted "+c.GetType().Name+"."+name);}
 static void Main(){
  var plain=new Plain();foreach(var n in new[]{"number","vector","reference","numbers","refs","hidden"})Accept(plain,n);
  Accept(new InheritedPlain(),"number");Accept(new InheritedPlain(),"flag");Accept(new Component(),"native");
  foreach(var n in new[]{"transient","readOnly","shared"})Reject(plain,n);
  foreach(var c in new Component[]{new Managed(),new Nested(),new Matrix(),new CustomData(),new CustomCollection()})Accept(c,"number","project");
  Accept(new DecimalData(),"number");Reject(new Managed(),"managed");Reject(new Nested(),"value");Reject(new Matrix(),"value");Reject(new CustomData(),"value");Reject(new CustomCollection(),"value");
  Reject(new PropertyBehavior(),"value");Accept(new PropertyBehavior(),"field","project");Check(PropertyBehavior.calls==0,"getter/setter ran during scope");
  Accept(new CallbackChild(),"number","project");Accept(new Serialization(),"number","project");Accept(new ExecutingChild(),"number","project");Accept(new Editing(),"number","project");Accept(new HelperBehavior(),"number","project");Accept(new StaticBehavior(),"number","project");
  var ma=new MaterialSetter{gameObject=go};
  var maScope=JObject.FromObject(BuildPropertyCheckpointScope(ma,Member(typeof(MaterialSetter),"m_objects"),0,"m_objects.Array.size",scene,true));
  Check((string)maScope["archiveScope"]=="project"&&(string)maScope["propertyPath"]=="m_objects"&&(string)maScope["serializedPropertyPath"]=="m_objects.Array.size","original MA array-size request checkpoint");
  Check(MaterialSetter.reads==0,"MA property accessor invoked during checkpoint");
  var field=Member(typeof(Plain),"number");SetMemberValue(plain,field,42);Check((int)GetMemberValue(plain,field)==42,"actual field setter");
  var refs=Member(typeof(Plain),"refs");SetMemberValue(plain,refs,new List<GameObject>{go});Check(ReferenceEquals(((List<GameObject>)GetMemberValue(plain,refs))[0],go),"reference list");
  GlobalObjectId.guid="other";Reject(plain,"number");GlobalObjectId.guid="guid";go.scene=new Scene{path="Assets/Other.unity",handle=17};Reject(plain,"number");
  Console.WriteLine("plain fields, native preservation, rejection and identity passed");
 }
}
'''.replace("HELPERS", helpers)
    assert "plain fields, native preservation, rejection and identity passed" in run(tmp_path, program)


def test_real_python_checkpoint_accepts_exact_component_scope_and_blocks_forgery(tmp_path):
    target = {"scope":"component","project":{"root":str(tmp_path)},"scene":{"assetPath":"Assets/Main.unity","guid":"scene","digest":"a"*64},"object":{"globalObjectId":"object","exactHierarchyPath":"Avatar/Data"},"component":{"globalObjectId":"component","type":"Plain"}}
    request = {"gameObjectPath":"Avatar/Data","componentType":"Plain","componentIndex":0,"propertyPath":"number","value":42}
    scope = {"schema":"vrcforge.component_property_checkpoint.v1","ownerKind":"scene","scenePath":"Assets/Main.unity","sceneGuid":"scene","sceneDigest":"b"*64,"objectGlobalObjectId":"object","componentGlobalObjectId":"component","componentType":"Plain","componentIndex":0,"propertyPath":"number","archiveAssetPaths":["Assets/Main.unity"]}
    archived=[]
    def call():
        return prepare_component_property_checkpoint(tmp_path,{"executionTarget":target},build_plan=lambda *a:[("vrc_set_property",request)],inspect_owner=lambda a:{"ok":True,"checkpointScope":scope},prepare=lambda root,checkpoint_files: archived.append(checkpoint_files) or {"ok":True})
    assert call()["ok"] is True
    assert archived == [["Assets/Main.unity"]]
    scope["componentGlobalObjectId"]="forged"
    assert call()["ok"] is False
    assert len(archived)==1
