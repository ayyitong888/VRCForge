import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { execFileSync } from "node:child_process";
import { resolve } from "node:path";
import ts from "typescript";

const root=resolve(import.meta.dirname,"..");
const text=process.argv.includes("--baseline")?execFileSync("git",["show","HEAD:src/App.tsx"],{cwd:root,encoding:"utf8"}):await readFile(resolve(root,"src/App.tsx"),"utf8");
const source=ts.createSourceFile("App.tsx",text,ts.ScriptTarget.Latest,true,ts.ScriptKind.TSX);
const functions=new Map();
function visit(node){if(ts.isFunctionDeclaration(node)&&node.name)functions.set(node.name.text,node.getText(source));ts.forEachChild(node,visit)}
visit(source);
let request,error;
const noop=()=>{};
const parent={id:"parent-chat",sessionId:"parent-session",projectPath:"C:/fixture-project",title:"Parent"};
const environment={
  activeChat:parent,activeProjectPath:parent.projectPath,outfitPackagePath:"",endpoint:"http://127.0.0.1:8098",runtimeConnected:true,
  pickSubAgentName:()=>"Reviewer",setActiveView:noop,setError:noop,setSubAgentError:value=>{error=value},
  clearSelectionMenu:noop,setSelectedSubAgentPanelOpen:noop,ensureActiveChat:()=>parent.id,
  updateChat:(_,update)=>update(parent),touchChat:chat=>chat,persistChatsNow:async()=>{},getChatById:()=>parent,
  createSubAgent:async(_,body)=>{request=body;return {task:{id:"task",childSessionId:"child",parentChatId:parent.id}}},
  setSelectedSubAgent:noop,setSubAgentList:noop,reconcileSubAgentHandoffs:async()=>{},loadSubAgents:async()=>{},
};
function entry(name){
  assert(functions.has(name));
  const js=ts.transpileModule(functions.get(name),{compilerOptions:{target:ts.ScriptTarget.ES2022}}).outputText;
  return new Function(...Object.keys(environment),`${js};return ${name}`)(...Object.values(environment));
}
const delegate=entry("startSubAgentTask");
await delegate(undefined,"Inspect the FX layers and explain the conflict.");
assert.equal(error,"");assert.equal(request.role,"llm_readonly","ordinary delegation must create a real LLM child");
assert.equal(request.task,"Inspect the FX layers and explain the conflict.");
await delegate("skill_delegate","Run the requested tool.","vrcforge_read_text_file");
assert.equal(request.role,"skill_delegate","explicit tool delegation retains its declared tool handler");
assert.equal(request.params.toolName,"vrcforge_read_text_file");
await entry("openSelectionInSubAgent")("Selected source excerpt");
assert.equal(error,"");assert.equal(request.role,"llm_readonly");
assert.deepEqual(request.selectedMaterials,[{content:{text:"Selected source excerpt"}}]);
assert.equal(request.params.selectedText,undefined,"selected context has one canonical request field");
assert.equal(request.parentSessionId,"parent-session");
console.log("PASS: real App entry functions route natural delegation and selected context to LLM child; explicit tool delegation and scoped context preserved.");
