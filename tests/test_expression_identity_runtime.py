from pathlib import Path
import sys,subprocess,os
sys.path.insert(0,'tests')
from test_wardrobe_avatar_identity_runtime import _extract,_public
def read_source(file):
 ref=os.environ.get('VRCFORGE_EXPRESSION_IDENTITY_GIT_REF')
 path='Assets/VRCForge/Editor/Generic/'+file
 return subprocess.check_output(['git','show',f'{ref}:{path}'],text=True,encoding='utf8') if ref else Path(path).read_text(encoding='utf8')

def test_expression_identity_rejects_ambiguous_names_before_mutation(tmp_path):
 p=tmp_path
 s=read_source('UnityAvatarPrimitiveCrud.cs');a=read_source('UnityAvatarAuthoringCrud.cs')
 methods='static class Param {'+_public(s,'private static void Apply(string action, VRCExpressionParameters')+'} static class Menu {'+_public(s,'private static int ResolveControlIndex')+_public(s,'private static VRCExpressionsMenu TryResolveMenu')+'} static class EnsureMenu {'+_public(a,'private static VRCExpressionsMenu FindMenuByPath')+_public(a,'private static VRCExpressionsMenu FindMenuByPathParts')+'} static class AvatarAuthoringCrudCore {'+_public(a,'internal static VRCExpressionParameters.ValueType ParseExpressionParameterType')+'}'
 methods+=('static class ValidationAnchor{}')
 if 'private static void ValidateRequest' in s:
  methods=methods.replace('static class Param {','static class Param {'+_extract(s,'private static void ValidateRequest'))
 ensure=a.split('public static class EnsureExpressionParameterTool',1)[1].split('var plan =',1)[0]
 start=ensure.index('var matchingParameters') if 'var matchingParameters' in ensure else ensure.index('var existing =')
 methods+='static class EnsureParameter {public static object Select(VRCExpressionParameters asset,string parameterName){'+ensure[start:]+'return existing;}}'
 program=r'''using System;using System.Collections.Generic;using System.Linq;using Newtonsoft.Json.Linq;
 namespace VRC.SDK3.Avatars.ScriptableObjects {
  public class VRCExpressionParameters {public enum ValueType{Int,Bool,Float} public class Parameter {public string name;public ValueType valueType;public float defaultValue;public bool saved,networkSynced;} public Parameter[] parameters;}
  public class VRCExpressionsMenu {public int GetInstanceID()=>GetHashCode();public List<Control> controls=new List<Control>(); public class Control {public enum ControlType{Toggle,SubMenu} public string name;public ControlType type;public VRCExpressionsMenu subMenu;}}
 }
 '''
 program='using VRC.SDK3.Avatars.ScriptableObjects;\n'+program+methods+r'''
 class Probe {static void Reject(Action call,string label){try{call();Console.WriteLine("ACCEPTED "+label);}catch(InvalidOperationException){Console.WriteLine("REJECTED "+label);}}
 static int Main(){
 var asset=new VRCExpressionParameters{parameters=new[]{new VRCExpressionParameters.Parameter{name="A"},new VRCExpressionParameters.Parameter{name="B"}}};
 Reject(()=>Param.Apply("rename",asset,JObject.Parse(@"{""parameterName"":""A"",""newName"":""B""}")),"rename collision");
 asset.parameters=new[]{new VRCExpressionParameters.Parameter{name="A"},new VRCExpressionParameters.Parameter{name="B"}};
 Reject(()=>Param.Apply("reorder",asset,JObject.Parse(@"{""orderNames"":[""A"",""A""]}")),"duplicate reorder");
 asset.parameters=new[]{new VRCExpressionParameters.Parameter{name="A"},new VRCExpressionParameters.Parameter{name="A"}};
 Reject(()=>EnsureParameter.Select(asset,"A"),"ensure duplicate parameter");
 var first=new VRCExpressionsMenu();var second=new VRCExpressionsMenu();var root=new VRCExpressionsMenu{controls=new List<VRCExpressionsMenu.Control>{new VRCExpressionsMenu.Control{name="Sub",type=VRCExpressionsMenu.Control.ControlType.SubMenu,subMenu=first},new VRCExpressionsMenu.Control{name="Sub",type=VRCExpressionsMenu.Control.ControlType.SubMenu,subMenu=second}}};
 Reject(()=>Menu.ResolveControlIndex(root,JObject.Parse(@"{""controlName"":""Sub""}")),"manage control ambiguity");
 Reject(()=>Menu.TryResolveMenu(root,"Sub"),"manage submenu ambiguity");
 Reject(()=>EnsureMenu.FindMenuByPath(root,"Sub",new HashSet<int>(),0),"ensure submenu ambiguity");
 asset.parameters=new[]{new VRCExpressionParameters.Parameter{name="A"},new VRCExpressionParameters.Parameter{name="B"}};
 Param.Apply("rename",asset,JObject.Parse(@"{""parameterName"":""A"",""newName"":""C""}"));
 Param.Apply("reorder",asset,JObject.Parse(@"{""orderNames"":[""B""]}"));if(string.Join(",",asset.parameters.Select(x=>x.name))!="B,C")throw new Exception("valid parameter operations changed");
 root.controls.RemoveAt(1);if(Menu.ResolveControlIndex(root,JObject.Parse(@"{""controlName"":""Sub""}"))!=0||Menu.TryResolveMenu(root,"Sub")!=first||EnsureMenu.FindMenuByPath(root,"Sub",new HashSet<int>(),0)!=first)throw new Exception("unique menu paths changed");
 Console.WriteLine("UNIQUE_PRESERVED");return 0;}}
 '''
 (p/'Program.cs').write_text(program,encoding='utf8');sdk=Path('C:/Program Files/dotnet/sdk');newtonsoft=sorted(sdk.glob('*/Newtonsoft.Json.dll'))[-1]
 (p/'probe.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup><ItemGroup><Reference Include="Newtonsoft.Json"><HintPath>'+str(newtonsoft)+'</HintPath></Reference></ItemGroup></Project>',encoding='utf8')
 r=subprocess.run(['dotnet','run','--project',str(p/'probe.csproj'),'--nologo'],capture_output=True,text=True,timeout=45)
 assert r.returncode==0,r.stdout+r.stderr
 assert r.stdout.count('REJECTED')==6,r.stdout+r.stderr
 assert 'UNIQUE_PRESERVED' in r.stdout
