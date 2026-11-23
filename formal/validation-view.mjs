export const title = "One Guardrails validation";

const stages = {
  ready: "Validation ready",
  precall: "Worker checks input",
  inflight: "Drex call in flight",
  startRecheck: "Replacement ready",
  replacementPrecall: "Worker checks replacement",
  rechecking: "Replacement call in flight",
  correction: "Correction pending",
  deliverDuplicate: "Duplicate failure pending",
};

export function state(v) {
  if (v.phase === "lateCall") return "Call started after cancellation";
  if (v.cancelled && v.phase === "done") return "Validation cancelled";
  if (v.cancelled) return "Cancelled while worker still checks input";
  if (v.phase !== "done") return stages[v.phase];
  if (v.retryResult === "pass" && v.retry !== "pass") return "Unchecked replacement accepted";
  if (v.retryResult === "fail") return "Replacement fails and Guardrails reasks";
  if (v.retryResult === "raise") return "Replacement raises";
  if (v.result === "raise") return "Error propagates";
  if (v.result === "pass") return "Pass returned";
  return "Guardrails applies correction";
}

const labels = {
  EnterExecutor: "worker begins",
  CancelBeforeStart: "caller cancels",
  CancelExecutorBeforeCall: "caller cancels",
  RefuseInput: "input is refused",
  CallDrex: "request commits and starts",
  CancelInFlight: "caller cancels",
  ReturnResult: "validator handles Drex reply",
  EnterReplacementExecutor: "worker begins replacement check",
  CallReplacement: "replacement commits and starts",
  ReturnReplacement: "validator handles replacement reply",
  CorrectOriginal: "Guardrails handles original failure",
  DeliverDuplicate: "Guardrails receives the failure again",
  Done: "validation ends",
};

export function step(action) {
  return labels[action];
}
