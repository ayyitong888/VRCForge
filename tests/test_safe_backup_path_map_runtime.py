"""Compile real backup mapping helpers against an isolated actual filesystem."""
from pathlib import Path
import os
import subprocess
from test_curve_fx_authoring_runtime_contract import method
from test_constraint_conversion_scope_runtime import run
ROOT=Path(__file__).resolve().parents[1]

def test_backup_map_rejects_noncanonical_paths_and_keeps_files_and_directories(tmp_path):
    path="Assets/VRCForge/Editor/ConsoleTools.cs"
    ref=os.environ.get("VRCFORGE_BACKUP_PATH_GIT_REF")
    raw=subprocess.check_output(["git","show",f"{ref}:{path}"],cwd=ROOT,text=True,encoding="utf-8") if ref else (ROOT/path).read_text(encoding="utf-8-sig")
    bodies="\n".join(method(raw,s) for s in ["private static void AddAssetPathToBackupMap", "private static void AddAssetPath(", "private static void AddFile(", "private static void AddMetaFileIfPresent", "private static string NormalizeAssetPath", "private static string ToProjectRelativePath"])
    run(tmp_path,r'''using System;using System.IO;using System.Linq;using System.Collections.Generic;
class BackupFileItem{public string project_relative_path,backup_relative_path,sha256;public bool is_meta;public int byte_count;}
class Probe{
BODIES
static void Main(){var root=Path.Combine(Path.GetTempPath(),Guid.NewGuid().ToString("N"));Directory.CreateDirectory(Path.Combine(root,"Assets","Folder"));Directory.CreateDirectory(Path.Combine(root,"ProjectSettings"));File.WriteAllText(Path.Combine(root,"ProjectSettings","Proof.txt"),"nonasset fixture");File.WriteAllText(Path.Combine(root,"Assets","Folder","Good.txt"),"asset fixture");File.WriteAllText(Path.Combine(root,"Assets","Folder","Good.txt.meta"),"meta fixture");
try{
foreach(var path in new[]{"Assets/../ProjectSettings/Proof.txt","Assets/./Folder/Good.txt","/Assets/Folder/Good.txt",@"C:\Assets\Folder\Good.txt",@"\\server\Assets\Good.txt","Assets//Folder/Good.txt"}){
var map=new Dictionary<string,BackupFileItem>();bool rejected=false;try{AddAssetPathToBackupMap(root,path,map,new List<string>());}catch(InvalidOperationException){rejected=true;}if(!rejected||map.Count!=0)throw new Exception("Noncanonical path was accepted: "+path);
var selected=new HashSet<string>();rejected=false;try{AddAssetPath(selected,path);}catch(InvalidOperationException){rejected=true;}if(!rejected||selected.Count!=0)throw new Exception("Selector erased invalid path evidence: "+path);
}
foreach(var path in new[]{"Assets/Folder/Good.txt","Assets/Folder/","Assets",@"Assets\Folder\Good.txt"}){var map=new Dictionary<string,BackupFileItem>();AddAssetPathToBackupMap(root,path,map,new List<string>());if(map.Count!=2||!map.ContainsKey("Assets/Folder/Good.txt")||!map.ContainsKey("Assets/Folder/Good.txt.meta"))throw new Exception("Valid file/directory backup changed: "+path);}
}finally{Directory.Delete(root,true);}}
}'''.replace("BODIES",bodies))
