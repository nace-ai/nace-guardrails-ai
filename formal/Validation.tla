------------------------------ MODULE Validation ------------------------------
EXTENDS Naturals, TLC
CONSTANT Variant
VARIABLES route, mode, action, reply, retry, phase, calls, result, corrections,
          retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck
vars == <<route, mode, action, reply, retry, phase, calls, result, corrections,
          retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

Routes == {"sync", "guard", "native", "executor"}
Modes == {"raise", "fail", "pass"}
Actions == {"noop", "fix_reask", "fix", "filter", "refrain", "reask", "custom", "exception"}
Replies == {"refused", "pass", "fail", "nonfinite", "unusable", "masked", "availability", "401"}
Observed(r) == CASE r = "nonfinite" /\ Variant = "noFinite" -> "pass"
                [] r = "401" /\ Variant = "map401" -> "availability"
                [] r = "masked" -> IF Variant = "maskedAvailability" THEN "availability" ELSE "unusable"
                [] OTHER -> r
Outcome(r, m) ==
  CASE r = "pass" -> "pass"
    [] r = "fail" -> IF Variant = "passAfterFail" THEN "pass" ELSE "fail"
    [] r = "availability" -> m
    [] r \in {"unusable", "nonfinite"} /\ m = "fail" -> "fail"
    [] OTHER -> "raise"
ExpectedFailure(r, m) == r = "fail" \/ (m = "fail" /\
                        r \in {"availability", "nonfinite", "unusable", "masked"})

Init ==
  /\ route \in Routes
  /\ mode \in Modes
  /\ action \in Actions
  /\ reply \in Replies
  /\ retry \in (IF action = "fix_reask" /\ ExpectedFailure(reply, mode)
                THEN {"pass", "fail", "unusable", "masked", "availability"} ELSE {"none"})
  /\ IF action = "fix_reask" THEN TRUE ELSE (reply = "fail" \/ action = "noop")
  /\ phase = "ready"
  /\ calls = 0 /\ result = "none" /\ corrections = 0
  /\ retryCalls = 0 /\ retryResult = "none" /\ retryCorrections = 0
  /\ cancelled = FALSE /\ cancelledBeforeCheck = FALSE

\* A bounded diagram slice. The main check always uses Init above.
ChartInit == Init /\ mode = "pass" /\ action = "fix_reask" /\ route \in {"native", "executor"}

\* An executor can already be running input validation when its awaiter is cancelled.
EnterExecutor ==
  /\ phase = "ready" /\ route = "executor"
  /\ phase' = "precall"
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, result, corrections,
                 retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

CancelBeforeStart ==
  /\ phase = "ready" /\ route \in {"native", "executor"}
  /\ phase' = "done" /\ cancelled' = TRUE
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, result, corrections,
                 retryCalls, retryResult, retryCorrections, cancelledBeforeCheck>>

CancelExecutorBeforeCall ==
  /\ phase \in {"precall", "replacementPrecall"} /\ ~cancelled /\ reply # "refused"
  /\ cancelled' = TRUE /\ cancelledBeforeCheck' = TRUE
  /\ UNCHANGED <<route, mode, action, reply, retry, phase, calls, result, corrections,
                 retryCalls, retryResult, retryCorrections>>

RefuseInput ==
  /\ phase \in {"ready", "precall"} /\ (route # "executor" \/ phase = "precall")
  /\ reply = "refused"
  /\ phase' = "done" /\ result' = "raise"
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, corrections,
                 retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

\* The cancellation-flag read is the sync request's commit point.
\* Per the contract, the read and SDK invocation are one modeled action.
CallDrex ==
  /\ phase = (IF route = "executor" THEN "precall" ELSE "ready")
  /\ reply # "refused"
  /\ calls' = IF cancelled /\ Variant # "removeCancelFlag" THEN 0 ELSE 1
  /\ phase' = IF cancelled THEN
                  IF Variant = "removeCancelFlag" THEN "lateCall" ELSE "done"
                ELSE "inflight"
  /\ UNCHANGED <<route, mode, action, reply, retry, result, corrections,
                 retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

CancelInFlight ==
  /\ phase \in {"inflight", "rechecking"} /\ route \in {"native", "executor"}
  /\ phase' = "done" /\ cancelled' = TRUE
  /\ result' = IF Variant = "catchCancelAsAvailability" /\ route = "native" /\
                    phase = "inflight" /\ mode # "raise"
               THEN Outcome("availability", mode)
               ELSE result
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, corrections,
                 retryCalls, retryResult, retryCorrections, cancelledBeforeCheck>>

\* Native response handling has no await between the SDK return and result construction.
ReturnResult ==
  /\ phase = "inflight"
  /\ result' = Outcome(Observed(reply), mode)
  /\ calls' = IF Variant = "callAgain" /\ result' = "fail" THEN calls + 1 ELSE calls
  /\ phase' = IF result' # "fail" THEN "done"
               ELSE IF action = "fix_reask" THEN "startRecheck" ELSE "correction"
  /\ UNCHANGED <<route, mode, action, reply, retry, corrections,
                 retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

EnterReplacementExecutor ==
  /\ phase = "startRecheck" /\ route = "executor"
  /\ phase' = "replacementPrecall"
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, result, corrections,
                 retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

CallReplacement ==
  /\ phase = (IF route = "executor" THEN "replacementPrecall" ELSE "startRecheck")
  /\ phase' = IF cancelled THEN
                  IF Variant = "removeCancelFlag" THEN "lateCall" ELSE "done"
                ELSE "rechecking"
  /\ retryCalls' = IF cancelled /\ Variant # "removeCancelFlag" THEN 0 ELSE 1
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, result, corrections,
                 retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

ReturnReplacement ==
  /\ phase = "rechecking"
  /\ retryResult' = IF Variant = "noReplacementCheck" THEN Outcome(Observed(retry), mode)
                    ELSE IF Variant = "allowErrorReplacement" /\ retry = "availability"
                         THEN Outcome(Observed(retry), mode)
                         ELSE IF retry = "pass" THEN "pass" ELSE "raise"
  /\ phase' = IF retryResult' = "raise" THEN "done" ELSE "correction"
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, result, corrections,
                 retryCalls, retryCorrections, cancelled, cancelledBeforeCheck>>

\* Guardrails corrects the original result, consuming a recheck result as an argument.
CorrectOriginal ==
  /\ phase = "correction"
  /\ phase' = IF Variant = "duplicateFail" /\ action = "custom"
               THEN "deliverDuplicate" ELSE "done"
  /\ corrections' = 1
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, result,
                 retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

DeliverDuplicate ==
  /\ phase = "deliverDuplicate"
  /\ phase' = "done" /\ corrections' = corrections + 1
  /\ UNCHANGED <<route, mode, action, reply, retry, calls, result,
                 retryCalls, retryResult, retryCorrections, cancelled, cancelledBeforeCheck>>

Done == phase \in {"done", "lateCall"} /\ UNCHANGED vars
Next == EnterExecutor \/ CancelBeforeStart \/ CancelExecutorBeforeCall \/ RefuseInput
        \/ CallDrex \/ CancelInFlight \/ ReturnResult \/ EnterReplacementExecutor
        \/ CallReplacement \/ ReturnReplacement \/ CorrectOriginal \/ DeliverDuplicate \/ Done

P1First == (phase = "done" /\ ~cancelled /\ retry = "none" /\
            ExpectedFailure(reply, mode) /\ result # "raise") => corrections = 1
P1Original == (phase = "done" /\ ~cancelled /\ ExpectedFailure(reply, mode) /\
               result # "raise" /\ retryResult # "raise") => corrections = 1
P1Replacement == (phase = "done" /\ ~cancelled /\ retryResult = "fail") => retryCorrections = 1
P1Accepted == (phase = "done" /\ ~cancelled /\ action = "fix_reask" /\
               retryCalls = 1 /\ retryResult = "pass") => retry = "pass"
P2 == /\ (result = "pass" => reply = "pass" \/ (reply = "availability" /\ mode = "pass"))
      /\ (retryResult = "pass" => retry = "pass" \/ (retry = "availability" /\ mode = "pass"))
P2Finite == reply = "nonfinite" => result # "pass"
P2Caller == reply = "401" => result # "pass"
P3 == cancelled => /\ phase # "lateCall"
                   /\ ~(phase = "done" /\ corrections > 0)
                   /\ (phase = "done" /\ retry = "none" => result = "none")
P3Flag == cancelledBeforeCheck => phase # "lateCall"
P3Native == route = "native" => P3
P6 == /\ calls <= 1 /\ retryCalls <= 1
      /\ (reply = "refused" => calls = 0)
      /\ (phase = "done" /\ reply # "refused" /\ ~cancelled => calls = 1)
=============================================================================
