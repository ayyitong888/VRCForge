"""Explicit HTTP 503 fixtures; never claim these are connection errors."""
import httpx
import pytest
from openai import InternalServerError
from test_runtime_planner_service import RetrySequenceModel, PlannerModelResult, service
from runtime_planner_service import RuntimePlannerService


def error(*, started=False):
    exc=InternalServerError("Internal server error",response=httpx.Response(503,request=httpx.Request("POST","https://fixture.invalid/responses")),body={"error":"internal"})
    exc.stream_started=started
    return exc


def run(errors, *, wait=lambda delay:True):
    model=RetrySequenceModel(errors,PlannerModelResult('{"action":"reply","reply":"ok"}'))
    events=[]
    plan=service(model=model).plan_agent_turn("reply",{},{},provider_retry_callback=events.append,provider_retry_wait=wait)
    return model,events,plan


def test_plain_http_503_can_recover_without_false_connection_or_capacity_claim():
    exc=error()
    assert RuntimePlannerService._planner_failure_code(exc)=="provider_request_failed"
    model,events,plan=run([exc])
    assert model.calls==2
    assert plan["nextStep"]=="done"
    assert events==[{"attempt":1,"max":5,"reason":"server_error","statusCode":503}]


def test_plain_http_503_exhausts_existing_five_retry_budget():
    model,events,plan=run([error() for _ in range(6)])
    assert model.calls==6
    assert plan["plannerFailure"]["retryAttempts"]==5
    assert plan["plannerFailure"]["retryDecision"]=="NO_MORE_RETRY"
    assert plan["plannerFailure"]["code"]=="provider_request_failed"
    assert plan["plannerFailure"]["providerError"]["statusCode"]==503
    assert [event["attempt"] for event in events]==[1,2,3,4,5,5]


@pytest.mark.parametrize("started",[True,None])
def test_started_or_unknown_stream_cannot_replay_503(started):
    model,events,plan=run([error(started=started)])
    assert model.calls==1 and events==[] and plan["plannerFailed"]


@pytest.mark.parametrize("marker",["attribute","body"])
def test_explicit_no_more_retry_stops_503(marker):
    exc=error()
    if marker=="attribute": exc.no_more_retry=True
    else: exc.body={"headers":{"x-retry-metadata":"NO_MORE_RETRY"}}
    model,events,plan=run([exc])
    assert model.calls==1
    assert plan["plannerFailure"]["retryDecision"]=="NO_MORE_RETRY"


def test_cancelled_wait_does_not_replay_503():
    model,events,plan=run([error()],wait=lambda delay:False)
    assert model.calls==1
    assert plan["plannerFailure"]["retryDecision"]=="CANCELLED"


def test_response_status_fallback_and_other_statuses():
    exc=RuntimeError("Internal server error")
    exc.response=httpx.Response(503)
    exc.stream_started=False
    assert RuntimePlannerService._provider_retry_reason(exc)=="server_error"
    for status in (400,401,403,404,422,500):
        exc.response=httpx.Response(status)
        assert RuntimePlannerService._provider_retry_reason(exc)==""
