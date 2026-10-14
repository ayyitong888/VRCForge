import assert from "node:assert/strict";
import { responseFromRuntimeTurnEvent } from "../src/hooks/use-runtime-turn-continuation.ts";

const usage = {
  schema: "vrcforge.context_usage.v1",
  exact: true,
  provider: "chatgpt",
  model: "gpt-6.1-sol",
  inputTokens: 44773,
  outputTokens: 148,
  totalTokens: 44921,
  requestCount: 3,
  cacheReadTokens: 13824,
};
const response = responseFromRuntimeTurnEvent({
  schema: "vrcforge.runtime_turn_event.v1",
  continuationSource: "question_answered",
  sessionId: "session-1",
  turnId: "turn-1",
  clientTurnId: "client-1",
  contextUsage: usage,
  plan: { reply: "蓝色", nextStep: "done" },
});

assert.deepEqual(response.contextUsage, usage, "question continuation must retain provider context usage");
assert.equal(response.plan.reply, "蓝色");
assert.equal(response.plan.nextStep, "done");

const plannerFailure = responseFromRuntimeTurnEvent({
  schema: "vrcforge.runtime_turn_event.v1",
  continuationSource: "foreground_completed",
  sessionId: "session-2",
  turnId: "turn-2",
  plan: {
    reply: "retry exhausted",
    plannerFailure: {
      code: "provider_request_failed",
      phase: "post_tool",
      transportPhase: "first_byte",
      retryable: true,
      retryAttempts: 5,
      retryDecision: "NO_MORE_RETRY",
      providerError: {
        statusCode: 429,
        type: "rate_limit_error",
        code: "rate_limit_exceeded",
        streamStarted: false,
        message: "must not cross",
        body: { secret: "must not cross" },
        headers: { authorization: "must not cross" },
        key: "must not cross",
        stack: "must not cross",
      },
    },
  },
});

assert.deepEqual(plannerFailure.plan.plannerFailure, {
  code: "provider_request_failed",
  phase: "post_tool",
  transportPhase: "first_byte",
  retryable: true,
  retryAttempts: 5,
  retryDecision: "NO_MORE_RETRY",
  providerError: {
    statusCode: 429,
    type: "rate_limit_error",
    code: "rate_limit_exceeded",
    streamStarted: false,
  },
}, "controlled planner failure must survive continuation projection");

const streamFailure = responseFromRuntimeTurnEvent({
  schema: "vrcforge.runtime_turn_event.v1",
  continuationSource: "foreground_completed",
  sessionId: "session-3",
  turnId: "turn-3",
  plan: {
    reply: "stream failed",
    plannerFailure: {
      code: "provider_stream_error",
      phase: "provider_request",
      retryable: false,
      retryAttempts: 0,
      retryDecision: "CANCELLED",
      providerError: {
        type: "stream_error",
        code: "stream_reset",
        streamStarted: true,
      },
    },
  },
});
assert.equal(streamFailure.plan.plannerFailure?.providerError?.streamStarted, true);
assert.equal(streamFailure.plan.plannerFailure?.providerError?.statusCode, undefined);

const hostileFailure = responseFromRuntimeTurnEvent({
  schema: "vrcforge.runtime_turn_event.v1",
  continuationSource: "foreground_completed",
  sessionId: "session-4",
  turnId: "turn-4",
  plan: {
    plannerFailure: {
      code: "bad value with spaces",
      phase: "<script>",
      transportPhase: "first_byte",
      retryable: "yes",
      retryAttempts: -1,
      retryDecision: "RETRY",
      providerError: {
        statusCode: 700,
        type: "bad type with spaces",
        code: "<secret>",
        streamStarted: "yes",
        message: "must not cross",
        body: { secret: "must not cross" },
        headers: { authorization: "must not cross" },
        key: "must not cross",
        stack: "must not cross",
      },
    },
  },
});
assert.deepEqual(hostileFailure.plan.plannerFailure, {
  transportPhase: "first_byte",
}, "untrusted planner failure fields must not cross continuation projection");
