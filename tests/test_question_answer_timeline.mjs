import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import Module from "node:module";
import { fileURLToPath } from "node:url";
import { build } from "esbuild";

const compiled = await build({
  stdin: { resolveDir: process.cwd(), loader: "tsx", contents: `
    import React from "react";
    import { renderToStaticMarkup } from "react-dom/server";
    import { ConversationCard } from "./src/components/chat/conversation-card";
    import { projectConversationSteeringMessages } from "./src/lib/chat-steering-projection";
    export { missingTimelineAnswerSources } from "./src/lib/chat-steering-projection";
    export { questionAnswerItem } from "./src/lib/question-answer";
    import { mergeConversationTimelineItems } from "./src/lib/chat-timeline-presentation";
    import { projectProcessRounds } from "./src/lib/chat-process-rounds";
    export function render(items, questions, open = true) {
      const steering = projectConversationSteeringMessages(mergeConversationTimelineItems(items), questions);
      const view = projectProcessRounds(steering.items);
      return view.items.map(item => renderToStaticMarkup(<ConversationCard item={item}
        questionAlreadyShown={item.type === "question_answer" && steering.placedQuestionIds?.has(item.questionId)}
        timelineQuestions={steering.questionMessages?.get(item.clientTurnId || item.response?.clientTurnId)}
        sharedProcess={{open,first:false,last:true,onOpenChange:()=>{},failures:0,live:false}} />)).join("");
    }
  ` }, bundle: true, write: false, platform: "node", format: "cjs", jsx: "automatic",
  external: ["react", "react-dom/server", "react/jsx-runtime", "i18next", "react-i18next"],
});
const filename = fileURLToPath(new URL("./question-chronology.cjs", import.meta.url));
const fixture = new Module(filename);
fixture.paths = Module._nodeModulePaths(process.cwd());
fixture._compile(compiled.outputFiles[0].text, filename);
const render = fixture.exports.render;
const stamp = s => "2026-11-19T13:00:" + String(s).padStart(2, "0") + "Z";
const event = (s, text, phase) => ({ id:"event-"+s, sequence:s, timestamp:stamp(s), kind:"assistant",
  payload:{ phase, summary:text, status:phase === "final_answer" ? "done" : "progress" } });
const question = {questionId:"q", originTurnId:"old-root", originClientTurnId:"exact-owner", createdAt:stamp(12), question:"Which color?"};
const answer = {type:"question_answer", id:"answer", questionId:"q", question:question.question, updatedAt:stamp(16), text:"Blue"};
const timeline = [event(10,"First update","commentary"), event(14,"Checked the scene","commentary"), event(20,"Final receipt","final_answer")];
const response = {sessionId:"session",clientTurnId:"exact-owner",turnId:"owner-turn",plan:{reply:"Final receipt",nextStep:"done"},timeline};
const owner = {type:"agent",id:"owner",response,timeline};
assert.deepEqual(fixture.exports.missingTimelineAnswerSources([answer,owner], []), ["q"]);
assert.deepEqual(fixture.exports.missingTimelineAnswerSources([answer,owner], [question]), []);
const storedAnswer = fixture.exports.questionAnswerItem({ ...question, answeredAt:stamp(16), updatedAt:stamp(28) }, "Blue");
assert.equal(storedAnswer.updatedAt, stamp(16));
assert.deepEqual(fixture.exports.missingTimelineAnswerSources([owner,storedAnswer], []), []);
function inOrder(html, words) {
  const offsets = words.map(word => html.indexOf(word));
  assert.ok(offsets.every((position, i) => position >= 0 && (!i || position > offsets[i-1])),
    "expected the question, answer and agent events in author order, got positions " + offsets);
}
function assertQuotedAnswer(html, prompt = "Which color?", value = "Blue") {
  assert.equal(html.split("data-question-answer-value=").length - 1, 1, "one user answer");
  assert.equal(html.split("data-question-answer-quote=").length - 1, 1, "one question reference inside the answer bubble");
  assert.equal(html.split(prompt).length - 1, 2, "one authored prompt plus its quote");
  inOrder(html, [prompt, "data-question-answer-question=", "data-question-answer-value=", value]);
  const reference = html.split("data-question-answer-question=")[1].split("</blockquote>")[0];
  assert.ok(reference.includes(prompt), "the reference contains the original question");
}
for (const item of [owner, {type:"streaming",id:"live",sessionId:"session",clientTurnId:"exact-owner",turnId:"owner-turn",text:"",timeline}]) {
  const html = render([answer,item],[question]);
  inOrder(html, ["First update","Which color?","Checked the scene","Blue","Final receipt"]);
  assertQuotedAnswer(html);
  const closed = render([answer,item],[question],false);
  inOrder(closed, ["Which color?","Blue","Final receipt"]);
  assertQuotedAnswer(closed);
}
const echoedTimeline = [event(10, "First update. Which color?", "commentary"), ...timeline.slice(1)];
const echoed = { ...owner, timeline: echoedTimeline, response: { ...response, timeline: echoedTimeline } };
for (const open of [true, false]) {
  const html = render([answer, echoed], [question], open);
  assertQuotedAnswer(html);
  inOrder(html, ["Which color?", "Blue", "Final receipt"]);
}
const laterTimeline = [event(23, "Next work began", "commentary"), event(29, "Next work finished", "final_answer")];
const later = { id: "second", type: "agent", timeline: laterTimeline,
  response: { ...response, clientTurnId: "second", turnId: "second-turn", timeline: laterTimeline, plan: { reply:"Next work finished" } } };
for (const time of [22, 25]) {
  const lateAnswer = { ...answer, updatedAt: stamp(time), ...{ originClientTurnId:question.originClientTurnId, askedAt:question.createdAt } };
  const html = render([owner, later, lateAnswer], []);
  inOrder(html, time === 22 ? ["Which color?", "Final receipt", "Blue", "Next work began"]
    : ["Which color?", "Final receipt", "Next work began", "Blue", "Next work finished"]);
  assertQuotedAnswer(html);
  const closed = render([owner, later, lateAnswer], [], false);
  assertQuotedAnswer(closed);
  inOrder(closed, ["Which color?", "Final receipt", "Blue", "Next work finished"]);
}
if (process.argv[2]) {
  const {chats:[chat]} = JSON.parse(readFileSync(process.argv[2], "utf8"));
  const {questions} = JSON.parse(readFileSync(process.argv[3], "utf8"));
  const item = chat.items.find(i => i.type === "question_answer");
  const q = questions.find(q => q.questionId === item.questionId);
  const original = chat.items.find(i => i.response?.clientTurnId === q.originClientTurnId);
  const comment = original.timeline.filter(e => e.kind === "assistant" && e.timestamp < q.createdAt).at(-1);
  const final = original.timeline.filter(e => e.kind === "assistant" && e.timestamp > item.updatedAt).at(-1);
  const originalView = render(chat.items, questions);
  inOrder(originalView, [comment.payload.summary.slice(0,12),q.question.slice(0,15),item.text.slice(0,8),final.payload.summary.slice(0,12)]);
  assertQuotedAnswer(originalView, q.question.slice(0,15), item.text.slice(0,8));
}
console.log("question and answer chronology: ok");
