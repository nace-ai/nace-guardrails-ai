export const title = "Two validations share one policy";

const stages = {
  ready: "Ready",
  inflight: "Drex call in flight",
  read: "Reply received in worker",
  done: "Result returned",
};

export function state(v) {
  return {
    "First validation": stages[v.phase[1]],
    "Second validation": stages[v.phase[2]],
  };
}

const labels = {
  Call: "request starts",
  ReadAnswer: "worker reads reply",
  BuildResult: "result returns",
  Done: "validations end",
};

export function step(action) {
  return labels[action];
}
