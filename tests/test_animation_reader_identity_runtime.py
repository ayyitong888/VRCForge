from pathlib import Path
import sys,subprocess,json,os
sys.path.insert(0,'tests')
from test_wardrobe_avatar_identity_runtime import _extract,_public
def read_source(relative):
    ref=os.environ.get('VRCFORGE_ANIMATION_READER_GIT_REF')
    return subprocess.check_output(['git','show',f'{ref}:{relative}'],text=True,encoding='utf8') if ref else Path(relative).read_text(encoding='utf-8-sig')

def test_animation_reader_selectors_reject_ambiguous_avatar_and_fx(tmp_path):
    folder=tmp_path
    classes=[]
    for name,file in [('Fx','ComponentTools.cs'),('Bindings','AssetTools.cs')]:
     source=read_source('Assets/VRCForge/Editor/'+file)
     methods=[_public(source,'private static '+sig) for sig in ['Component ResolveAvatarDescriptor','AnimatorController ResolveFxController','Type FindType','bool IsSceneObject','string GetTransformPath','string NormalizePath','object GetMemberValue']]
     classes.append('static class '+name+' {'+'\n'.join(methods)+'}')
    shared=read_source('Assets/VRCForge/Editor/Generic/UnityAvatarAuthoringCrud.cs')
    classes.append('static class AvatarAuthoringCrudCore {'+'\n'.join(_public(shared,'internal static '+sig) for sig in ['VRC.SDK3.Avatars.Components.VRCAvatarDescriptor ResolveAvatarDescriptor'.replace('VRC.SDK3.Avatars.Components.',''),'string GetTransformPath','string NormalizePath','AnimatorController GetFxController'])+'}')
    program=r'''using VRC.SDK3.Avatars.Components;
    using System;using System.Collections;using System.Collections.Generic;using System.Linq;using System.Globalization;
    using UnityEngine;using UnityEditor;using UnityEditor.Animations;
    namespace UnityEngine {
     public class Object{} public class Scene{public bool isLoaded=true;public bool IsValid()=>true;}
     public class GameObject:Object{public Scene scene=new Scene();public string name;}
     public class Transform:Object{public string name;public Transform parent;}
     public class Component:Object{public GameObject gameObject;public Transform transform;public string name=>gameObject.name;}
     public static class Resources{public static List<Component> Items=new List<Component>();public static T[] FindObjectsOfTypeAll<T>()=>Items.OfType<T>().ToArray();public static Object[] FindObjectsOfTypeAll(Type type)=>Items.Where(type.IsInstanceOfType).Cast<Object>().ToArray();}
    }
    namespace UnityEditor {public static class EditorUtility{public static bool IsPersistent(object item)=>false;}}
    namespace UnityEditor.Animations{public class RuntimeAnimatorController{} public class AnimatorController:RuntimeAnimatorController{public string name;}}
    namespace VRC.SDK3.Avatars.Components{public class VRCAvatarDescriptor:Component{public enum AnimLayerType{FX} public class CustomAnimLayer{public AnimLayerType type=AnimLayerType.FX;public RuntimeAnimatorController animatorController;} public CustomAnimLayer[] baseAnimationLayers;}}
    '''+ '\n'.join(classes)+r'''
    class Probe {
     static Component Avatar(string path){Transform t=null;foreach(var part in path.Split('/')) t=new Transform{name=part,parent=t};var d=new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor{transform=t,gameObject=new GameObject{name=t.name}};Resources.Items.Add(d);return d;}
     static void Run(string name,Func<string,Component> resolve,Func<Component,AnimatorController> fx){
     foreach(var item in new[]{("empty-many","",new[]{"Root/A","Root/B"}),("duplicate-leaf","Avatar",new[]{"A/Avatar","B/Avatar"}),("duplicate-full","Root/Avatar",new[]{"Root/Avatar","Root/Avatar"})}){
     Resources.Items.Clear();foreach(var p in item.Item3)Avatar(p);try{var found=resolve(item.Item2);Console.WriteLine(name+" "+item.Item1+" ACCEPTED "+found.name);}catch(Exception e){Console.WriteLine(name+" "+item.Item1+" REJECTED "+e.Message);}}
     Resources.Items.Clear();var d=Avatar("Unique");((VRC.SDK3.Avatars.Components.VRCAvatarDescriptor)d).baseAnimationLayers=new[]{new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor.CustomAnimLayer{animatorController=new AnimatorController{name="first"}},new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor.CustomAnimLayer{animatorController=new AnimatorController{name="second"}}};try{Console.WriteLine(name+" duplicate-FX ACCEPTED "+fx(d).name);}catch(Exception e){Console.WriteLine(name+" duplicate-FX REJECTED "+e.Message);}
     var avatar=(VRC.SDK3.Avatars.Components.VRCAvatarDescriptor)d;
     avatar.baseAnimationLayers=new[]{avatar.baseAnimationLayers[0]};
     if(resolve("")!=d || resolve("Unique")!=d || fx(d).name!="first") throw new Exception("unique selection regressed");
     Console.WriteLine("UNIQUE_PRESERVED "+name);
     avatar.baseAnimationLayers=new VRC.SDK3.Avatars.Components.VRCAvatarDescriptor.CustomAnimLayer[0];
     try{fx(d);throw new Exception("missing FX accepted");}catch(InvalidOperationException e){if(e.Message!="No FX AnimatorController found on the avatar.")throw;Console.WriteLine("NO_FX_PRESERVED "+name);}
     }
     static int Main(){Run("scan_fx_animator",Fx.ResolveAvatarDescriptor,Fx.ResolveFxController);Run("scan_animation_bindings",Bindings.ResolveAvatarDescriptor,Bindings.ResolveFxController);return 0;}
    }
    '''
    (folder/'Program.cs').write_text(program,encoding='utf8');(folder/'probe.csproj').write_text('<Project Sdk="Microsoft.NET.Sdk"><PropertyGroup><OutputType>Exe</OutputType><TargetFramework>netcoreapp3.1</TargetFramework></PropertyGroup></Project>',encoding='utf8')
    r=subprocess.run(['dotnet','run','--project',str(folder/'probe.csproj'),'--nologo'],text=True,capture_output=True,timeout=45)
    assert r.returncode==0,r.stdout+r.stderr
    assert r.stdout.count('REJECTED')==8,r.stdout+r.stderr
    assert r.stdout.count('UNIQUE_PRESERVED')==2,r.stdout+r.stderr
    assert r.stdout.count('NO_FX_PRESERVED')==2,r.stdout+r.stderr
