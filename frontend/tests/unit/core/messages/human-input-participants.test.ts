import { expect, test } from "@rstest/core";

import {
  buildCouncilParticipantEdits,
  createHumanInputOptionResponse,
  parseHumanInputRequest,
  parseHumanInputResponse,
  participantsForCouncilDepth,
  type CouncilParticipant,
} from "@/core/messages/human-input";

const participant = (overrides: Partial<CouncilParticipant> = {}) => ({
  id: "position-1",
  role: "position",
  role_label: "Independent position",
  agent_name: "general-purpose",
  via_generalist: false,
  focus: "trial statistics",
  model: "gpt-5.6-sol",
  model_options: ["gpt-5.6-sol", "claude-fable-5"],
  max_tokens: 400000,
  max_tokens_min: 10000,
  max_tokens_max: 2000000,
  reasoning: "standard",
  reasoning_options: ["standard", "extended"],
  instructions: "Argue from the trial statistics.",
  ...overrides,
});

const preflightRequest = {
  version: 1,
  kind: "human_input_request",
  source: "ask_clarification",
  request_id: "dbtl-council__cyc-1__abc",
  clarification_type: "council_preflight",
  question: "How much debate should this design get?",
  input_mode: "single_choice",
  options: [
    { id: "light", label: "Light debate", value: "light" },
    { id: "adjust", label: "Adjust the roster first", value: "adjust" },
  ],
  council_participants: [
    participant(),
    participant({ id: "chair", role: "chair", role_label: "Chair" }),
  ],
};

test("parseHumanInputRequest keeps well-formed meeting participants", () => {
  const request = parseHumanInputRequest(preflightRequest);
  expect(request?.council_participants).toHaveLength(2);
  expect(request?.council_participants?.[0]).toMatchObject({
    id: "position-1",
    model: "gpt-5.6-sol",
    max_tokens: 400000,
    instructions: "Argue from the trial statistics.",
  });
});

test("a malformed participant is skipped without failing the card", () => {
  const request = parseHumanInputRequest({
    ...preflightRequest,
    council_participants: [
      { id: "", agent_name: "x", max_tokens: 1 },
      participant({ id: "chair" }),
    ],
  });
  expect(request).not.toBeNull();
  expect(request?.council_participants?.map((entry) => entry.id)).toEqual([
    "chair",
  ]);
});

test("nothing usable falls back to a card without the editor", () => {
  const request = parseHumanInputRequest({
    ...preflightRequest,
    council_participants: [{ nonsense: true }],
  });
  expect(request).not.toBeNull();
  expect(request?.council_participants).toBeUndefined();
});

test("buildCouncilParticipantEdits submits only what changed", () => {
  const participants = [
    participant(),
    participant({ id: "chair", role: "chair", role_label: "Chair" }),
  ] as CouncilParticipant[];
  const edits = buildCouncilParticipantEdits(participants, {
    "position-1": {
      model: "claude-fable-5",
      maxTokens: "400000",
      reasoning: "extended",
      instructions: "Argue from the trial statistics.",
    },
    chair: {
      model: "gpt-5.6-sol",
      maxTokens: "150000",
      reasoning: "standard",
      instructions: "Argue from the trial statistics.",
    },
  });
  expect(edits).toEqual({
    "position-1": { model: "claude-fable-5", reasoning: "extended" },
    chair: { max_tokens: 150000 },
  });
});

test("an untouched editor submits no participants key at all", () => {
  const participants = [participant()] as CouncilParticipant[];
  const edits = buildCouncilParticipantEdits(participants, {
    "position-1": {
      model: "gpt-5.6-sol",
      maxTokens: "400000",
      reasoning: "standard",
      instructions: "Argue from the trial statistics.",
    },
  });
  expect(edits).toBeUndefined();

  const request = parseHumanInputRequest(preflightRequest)!;
  const response = createHumanInputOptionResponse(
    request,
    { id: "light", label: "Light debate", value: "light" },
    edits,
  );
  expect("participants" in response).toBe(false);
});

test("participant edits round-trip through the response parser", () => {
  const request = parseHumanInputRequest(preflightRequest)!;
  const response = createHumanInputOptionResponse(
    request,
    { id: "light", label: "Light debate", value: "light" },
    { "position-1": { model: "claude-fable-5", max_tokens: 90000 } },
  );
  const parsed = parseHumanInputResponse(response);
  expect(parsed).toMatchObject({
    response_kind: "option",
    option_id: "light",
    participants: {
      "position-1": { model: "claude-fable-5", max_tokens: 90000 },
    },
  });
});

test("a debate depth exposes only the participants that will run", () => {
  const participants = [
    participant({ id: "position-1", focus: "first" }),
    participant({ id: "position-2", focus: "second" }),
    participant({ id: "position-3", focus: "third" }),
    participant({ id: "red-team", role: "red_team", role_label: "Red team" }),
    participant({ id: "chair", role: "chair", role_label: "Chair" }),
  ] as CouncilParticipant[];

  expect(
    participantsForCouncilDepth(participants, "light").map((entry) => entry.id),
  ).toEqual(["position-1", "red-team", "chair"]);
  expect(
    participantsForCouncilDepth(participants, "medium").map(
      (entry) => entry.id,
    ),
  ).toEqual(["position-1", "position-2", "red-team", "chair"]);
  expect(
    participantsForCouncilDepth(participants, "heavy").map((entry) => entry.id),
  ).toEqual(["position-1", "position-2", "position-3", "red-team", "chair"]);
  expect(participantsForCouncilDepth(participants, "human_input")).toEqual([]);
});
