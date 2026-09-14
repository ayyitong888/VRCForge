"""Run exact production pre-open block against bounded scene/asset I/O doubles."""
import json,os,shutil,subprocess
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]

def test_restore_imports_all_exact_scenes_before_opening(tmp_path):
    source=(ROOT/"Assets/VRCForge/Editor/CheckpointRecoveryTool.cs").read_text(encoding="utf-8")
    start=source.index("var restoredScenes = new List<Scene>();")
    opening=source.index("try",start)
    block=source[opening+len("try"):source.index("catch (Exception reopenError)",opening)]
    guard_start=source.index("foreach (var path in scenes)",source.index('if (string.IsNullOrWhiteSpace(activeScenePath) && scenes.Count > 0)'))
    guard=source[guard_start:source.index('transactionHandle =',guard_start)]
    program=r"""
using System;using System.IO;using System.Linq;using System.Collections.Generic;
class Scene {public string path;}
enum OpenSceneMode{Additive} [Flags]enum ImportAssetOptions {ForceUpdate=1,ForceSynchronousImport=2}
class Receipt {public string Asset,Status;public object After;}
class CheckpointPrepareTool {public static string Root;public static string ProjectRoot()=>Root;}
class VRCForgeToolResult {public static object Failed(string s,object p)=>false;}
static class AssetDatabase {public static List<string> Events=new List<string>(); public static void ImportAsset(string p,ImportAssetOptions options){if(options!=(ImportAssetOptions.ForceUpdate|ImportAssetOptions.ForceSynchronousImport))throw new Exception("options");Events.Add("import:"+p);}}
static class EditorSceneManager {public static Scene OpenScene(string p,OpenSceneMode mode){AssetDatabase.Events.Add("open:"+p);return new Scene{path=p};}}
class Probe {static object DescribeScene(string path)=>null;
static object Validate(List<string> scenes){string phase="reload";
"""+guard+r"""return true;}
static void Reload(List<string> scenes){var receipts=scenes.Select(p=>new Receipt{Asset=p}).ToList();var restoredScenes=new List<Scene>();
"""+block+r"""}
static int Main(string[] args){CheckpointPrepareTool.Root=args[0];Directory.CreateDirectory(Path.Combine(args[0],"Assets"));
foreach(var p in new[]{"Assets/One.unity","Assets/Two.unity","Assets/Bad.mat"})File.WriteAllText(Path.Combine(args[0],p),"test");
for(int count=0;count<=2;count++){var scenes=new[]{"Assets/One.unity","Assets/Two.unity"}.Take(count).ToList();AssetDatabase.Events.Clear();Reload(scenes);
var expected=scenes.Select(p=>"import:"+p).Concat(scenes.Select(p=>"open:"+p));if(!expected.SequenceEqual(AssetDatabase.Events))throw new Exception("order:"+string.Join(",",AssetDatabase.Events));}
if(!(bool)Validate(new List<string>{"Assets/One.unity","Assets/Two.unity"}))throw new Exception("valid rejected");
foreach(var p in new[]{"Assets/Bad.mat","Assets/../Assets/One.unity","Assets/Missing.unity"})if((bool)Validate(new List<string>{p}))throw new Exception("invalid accepted:"+p);
Console.WriteLine("PASS scene-order-and-paths");return 0;}}
"""
    base=Path(os.environ.get("ProgramFiles","C:/Program Files"))/"dotnet"
    compiler=sorted((base/"sdk").glob("*/Roslyn/bincore/csc.dll"))[-1]
    refs=sorted((base/"packs/Microsoft.NETCore.App.Ref").glob("*/ref/netcoreapp3.1"))[-1]
    dotnet=shutil.which("dotnet");dll=tmp_path/"Probe.dll"
    (tmp_path/"Probe.cs").write_text(program,encoding="utf-8")
    cmd=[dotnet,str(compiler),"-nologo","-target:exe","-nostdlib+",f"-out:{dll}"]+[f"-r:{p}" for p in refs.glob("*.dll")]+[str(tmp_path/"Probe.cs")]
    compiled=subprocess.run(cmd,capture_output=True,text=True,timeout=60)
    assert compiled.returncode==0,compiled.stdout+compiled.stderr
    (tmp_path/"Probe.runtimeconfig.json").write_text(json.dumps({"runtimeOptions":{"tfm":"netcoreapp3.1","framework":{"name":"Microsoft.NETCore.App","version":"3.1.0"}}}),encoding="utf-8")
    result=subprocess.run([dotnet,str(dll),str(tmp_path)],capture_output=True,text=True,timeout=30)
    assert result.returncode==0,result.stdout+result.stderr
    # Successful close loop and its failure return precede the import/open block.
    close=source.index("var closedBeforeReload")
    assert close < start and "return VRCForgeToolResult.Failed(" in source[close:start]
    assert "SaveAssets" not in block and "Refresh(" not in block
