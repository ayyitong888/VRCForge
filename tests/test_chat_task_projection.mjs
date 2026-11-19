import assert from 'node:assert/strict';
import {build} from 'esbuild';
import {createRequire} from 'node:module';
const bundle=await build({entryPoints:['src/lib/chat-task-projection.ts','src/lib/chat-process-rounds.ts','src/hooks/use-runtime-turn-continuation.ts'],bundle:true,write:false,platform:'node',format:'cjs',outdir:'unused',external:['react']});
const require=createRequire(import.meta.url);
const modules=bundle.outputFiles.map(file=>{const m={exports:{}};new Function('require','module','exports',file.text)(require,m,m.exports);return m.exports;});
const {projectConversationTasks:project}=modules.find(m=>m.projectConversationTasks);
const {projectProcessRounds}=modules.find(m=>m.projectProcessRounds);
const {projectAgentQuestionContinuation:questionProjection,questionContinuationDisplay}=modules.find(m=>m.projectAgentQuestionContinuation);
const user=(id)=>({id,type:'user',text:id,clientTurnId:id});
const agent=(id,nextStep='needs_user_action',extra={})=>({id:'reply-'+id,type:'agent',response:{clientTurnId:id,turnId:'backend-'+id,sessionId:'s',plan:{nextStep},...extra}});
const answer=(qid)=>({id:'answer-'+qid,type:'question_answer',questionId:qid,text:'answered'});
{
  const at = minute => new Date(Date.UTC(2026, 10, 19, 13, minute)).toISOString();
  const original = agent('original', 'done', {startedAt: at(10)});
  const later = agent('later', 'done', {startedAt: at(30)});
  const response = {...answer('decision'), updatedAt: at(20)};
  const submitted = {...user('new-message'), createdAt: at(25)};
  const revision = {id: 'revision', type: 'approval_revision', requestedAt: at(26)};
  const decision = {id: 'result-approval-' + Date.parse(at(27)), type: 'result', approvalId: 'approval'};
  const untimed = {id: 'legacy-note', type: 'error', text: 'Historical note'};
  const recovered = projectProcessRounds([decision, untimed, response, later, submitted, revision, original]);
  assert.deepEqual(recovered.items, [original, untimed, response, submitted, revision, decision, later],
    'recovering a delayed receipt preserves the author times of all dated messages');
}
const ask=(tool,qid,blocking=true)=>({tool,result:{ok:true,question:{questionId:qid,status:'pending',blocking}}});
const parked=agent('cid','waiting_for_tool',{turnId:'original'});
const retry=agent('cid','waiting_for_tool',{turnId:'retry'}), next=agent('next','done');
const child=agent('opaque-child','waiting_for_tool',{parentClientTurnId:'cid', parentTurnId:'original'});
const grandchild=agent('opaque-grandchild','done',{parentTurnId:child.response.turnId});
const otherSession=agent('foreign','done',{parentTurnId:'original',sessionId:'different'});
const lateQuestion=agent('cid:question:late','done',{continuationSource:'question_answered'});
const wrongParent=agent('wrong','done',{parentTurnId:'missing',parentClientTurnId:'cid'});
const lineage=projectProcessRounds([grandchild,retry,next,child,parked,otherSession,lateQuestion,wrongParent]);
assert.deepEqual(lineage.items,[parked,retry,next,child,grandchild,otherSession,lateQuestion,wrongParent]);
assert.equal(lineage.rounds.size,3,'exact backend retry, same-session, explicit blocking-question policy');
assert.equal(lineage.rounds.get(grandchild).first,parked);
assert.equal(lineage.rounds.get(child).last,grandchild);
const waiting=agent('waiting','needs_user_action');
const answerDispatch=agent('opaque-answer','done',{continuationSource:'question_answered'});
assert.equal(projectProcessRounds([waiting,answer('q'),answerDispatch],'s').rounds.size,0,
  'an answer that starts fresh work has no explicit parent even if the old transcript recorded a wait');
answerDispatch.response.parentTurnId=waiting.response.turnId;
assert.equal(projectProcessRounds([waiting,answer('q'),answerDispatch],'s').rounds.get(answerDispatch).first,waiting);
for (const source of ['shell_process_finished','sub_agent_finished','approval_finished','approval_revision_requested']) {
  const continued=agent('opaque-'+source,'done',{parentClientTurnId:'cid',continuationSource:source});
  assert.equal(projectProcessRounds([parked,user('separate-input'),continued]).rounds.get(continued).first,parked);
}
const first=user('first'), pending=agent('first','done',{steps:[ask('vrcforge_call_user','q')]});
assert.equal(project([first,pending,user('after-pending')],new Map()).startsAfter.size,0,'blocking call_user question prevents a false done boundary');
const partial=agent('first','interrupted',{continuationSource:'foreground_interrupted',status:'interrupted'});
assert.equal(project([first,partial,user('retry')],new Map()).startsAfter.size,0,'transport failure does not complete the logical task');
assert.equal(project([first,agent('first','needs_user_action',{plan:{nextStep:'needs_user_action',taskCompletion:{status:'completed'}}}),user('answer-followup')],new Map()).startsAfter.size,0,'question/tool taskCompletion is not explicit Agent completion');
const a=user('a'), doneA=agent('a','done',{steps:[ask('call_user','late-q',false)]}), b=user('b'), runningB=agent('b');
const late=answer('late-q'), continuation=agent('opaque-runtime-id');
const stillB=user('b-followup');
const q={questionId:'late-q',sessionId:'s',status:'answered',blocking:false,originClientTurnId:'a',runtimeContinuation:{questionId:'late-q',clientTurnId:'opaque-runtime-id'}};
const p=project([a,doneA,b,runningB,late,continuation,stillB],new Map(),[q]);
assert.equal(p.taskIdByItem.get(late),p.taskIdByItem.get(a),'late answer attaches by trusted question origin');
assert.equal(p.taskIdByItem.get(continuation),p.taskIdByItem.get(a),'opaque continuation attaches by explicit question relation without CID parsing');
assert.equal(p.taskIdByItem.get(stillB),p.taskIdByItem.get(b),'late old-task answer does not hijack the newer active task');
assert.notEqual(p.taskIdByItem.get(b),p.taskIdByItem.get(a),'explicit done starts a genuinely new user task');
assert.equal(project([a,agent('a','cancelled'),b],new Map()).startsAfter.size,1,'explicit user Stop cancellation is a task boundary');
const activeQuestion={questionId:'q',sessionId:'s',status:'answered',runtimeContinuationStatus:'queued',runtimeContinuationClientTurnId:'original-cid',runtimeContinuationTurnId:'original-turn'};
const projected=questionProjection(activeQuestion,[{sessionId:'s',clientTurnId:'foreign:question:q',turnId:'wrong',status:'completed'},{sessionId:'s',clientTurnId:'original-cid',turnId:'wrong-backend-turn',status:'completed'},{sessionId:'s',clientTurnId:'original-cid',turnId:'original-turn',status:'running'}]);
assert.equal(projected.runtimeContinuation.clientTurnId,'original-cid','explicit same-runtime answer binding wins over a legacy-looking CID');
assert.equal(projected.runtimeContinuation.status,'running','answer receipt does not mark the running Agent complete');
assert.equal(questionContinuationDisplay({status:'answered',runtimeContinuation:{status:'queued'}}),'settled','an answered question without an admitted runtime identity cannot create phantom active work');
assert.equal(questionContinuationDisplay(projected),'active','an exact original-CID answer remains visibly active until the actual runtime finishes');
const savedOnly=questionProjection({
  questionId:'q',sessionId:'s',status:'answered',runtimeContinuationStatus:'settled_without_dispatch',
  runtimeContinuation:{clientTurnId:'old:question:q',status:'running'},
},[{sessionId:'s',clientTurnId:'old:question:q',turnId:'stale-turn',status:'running'}]);
assert.equal(questionContinuationDisplay(savedOnly),'settled','a saved-only answer must not revive a stale continuation');
assert.equal(savedOnly.runtimeContinuation.clientTurnId,undefined);
console.log('PASS logical tasks: call_user blocking, nonblocking independent work, explicit opaque question identity, late answers, taskCompletion receipt, Stop, exact active CID');
