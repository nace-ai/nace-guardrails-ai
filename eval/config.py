MODELS = ("drex-max-v1.1", "drex-v2.0")
QUESTION = "Is this message outside the scope of a banking support assistant?"
CRITERIA = {
    "true": "The user asks about something outside banking support.",
    "false": "The user asks for help with a bank account, card, payment, or other banking service.",
}
SEED = 457
PARTS = ("validation", "test")
# Positive means out of scope. A positive decision fails the policy.
LABELS = {"banking": 0, "oos": 1, "other": 1}
TARGET_COUNTS = {
    "validation": {"banking": 300, "oos": 100, "other": 300},
    "test": {"banking": 450, "oos": 1000, "other": 450},
}
