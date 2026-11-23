------------------------------ MODULE Concurrency ------------------------------
EXTENDS Naturals, TLC
CONSTANT Variant
VARIABLES route, phase, lastAnswer, results
vars == <<route, phase, lastAnswer, results>>
Attempts == 1..2

Init == /\ route \in {"sync", "native", "executor"}
        /\ phase = [i \in Attempts |-> "ready"]
        /\ lastAnswer = 0
        /\ results = [i \in Attempts |-> 0]

Call(i) ==
  /\ phase[i] = "ready"
  /\ phase' = [phase EXCEPT ![i] = "inflight"]
  /\ UNCHANGED <<route, lastAnswer, results>>

\* Sync threads can switch between lines of _result. Native tasks cannot.
ReadAnswer(i) ==
  /\ route # "native" /\ phase[i] = "inflight"
  /\ phase' = [phase EXCEPT ![i] = "read"]
  /\ lastAnswer' = IF Variant = "sharedAnswer" THEN i ELSE lastAnswer
  /\ UNCHANGED <<route, results>>

BuildResult(i) ==
  /\ phase[i] = (IF route = "native" THEN "inflight" ELSE "read")
  /\ phase' = [phase EXCEPT ![i] = "done"]
  /\ results' = [results EXCEPT ![i] = IF route = "native" \/ Variant # "sharedAnswer"
                                      THEN i ELSE lastAnswer]
  /\ UNCHANGED <<route, lastAnswer>>

CallDrex == \E i \in Attempts : Call(i)
ReadDrexAnswer == \E i \in Attempts : ReadAnswer(i)
ReturnResult == \E i \in Attempts : BuildResult(i)
Done == (\A i \in Attempts : phase[i] = "done") /\ UNCHANGED vars
Next == CallDrex \/ ReadDrexAnswer \/ ReturnResult \/ Done
P8 == \A i \in Attempts : phase[i] = "done" => results[i] = i
===============================================================================
