import type { Message } from "@langchain/langgraph-sdk";

export type HumanInputMode =
  | "free_text"
  | "single_choice"
  | "choice_with_other";

export type HumanInputOption = {
  id: string;
  label: string;
  value: string;
};

export type DbtlCycleSetup = {
  title: string;
  objective: string;
  success_criteria: string;
};

export type SetupQuestionOption = {
  id: string;
  label: string;
  description?: string;
};

/**
 * One step of the DBTL setup wizard, written by the model.
 *
 * Present only on `cycle_setup` cards. A card without it still renders as a
 * plain free-text question, so an older backend degrades rather than breaks.
 */
export type SetupQuestion = {
  id: string;
  question: string;
  why?: string;
  options?: SetupQuestionOption[];
  recommended_option_id?: string;
  recommendation?: string;
  grounded?: boolean;
};

export type HumanInputRequest = {
  version: 1;
  kind: "human_input_request";
  source: "ask_clarification" | string;
  request_id: string;
  tool_call_id?: string;
  clarification_type?: string;
  title?: string;
  question: string;
  context?: string | null;
  input_mode: HumanInputMode;
  options?: HumanInputOption[];
  dbtl_cycle_setup?: DbtlCycleSetup;
  setup_questions?: SetupQuestion[];
};

export type HumanInputResponse =
  | {
      version: 1;
      kind: "human_input_response";
      source: string;
      request_id: string;
      response_kind: "option";
      option_id: string;
      value: string;
    }
  | {
      version: 1;
      kind: "human_input_response";
      source: string;
      request_id: string;
      response_kind: "text";
      value: string;
    };

export type HumanInputThreadState = {
  answeredResponses: Map<string, HumanInputResponse>;
  latestOpenRequestId: string | null;
};

export function shouldClearPendingHumanInputOnThreadError({
  currentError,
  pendingRequestCount,
  previousError,
}: {
  currentError: unknown;
  pendingRequestCount: number;
  previousError: unknown;
}) {
  return (
    pendingRequestCount > 0 &&
    currentError != null &&
    !Object.is(currentError, previousError)
  );
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

function isNonEmptyString(value: unknown): value is string {
  return typeof value === "string" && value.trim().length > 0;
}

function isHumanInputMode(value: unknown): value is HumanInputMode {
  return (
    value === "free_text" ||
    value === "single_choice" ||
    value === "choice_with_other"
  );
}

function readOptionalString(value: unknown) {
  return typeof value === "string" ? value : undefined;
}

function parseOptions(value: unknown): HumanInputOption[] | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (!Array.isArray(value)) {
    return undefined;
  }

  const options: HumanInputOption[] = [];
  for (const option of value) {
    if (!isRecord(option)) {
      return undefined;
    }
    const id = option.id;
    const label = option.label;
    const optionValue = option.value;
    if (
      !isNonEmptyString(id) ||
      !isNonEmptyString(label) ||
      typeof optionValue !== "string"
    ) {
      return undefined;
    }
    options.push({ id, label, value: optionValue });
  }
  return options;
}

function parseDbtlCycleSetup(value: unknown): DbtlCycleSetup | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (
    !isRecord(value) ||
    !isNonEmptyString(value.title) ||
    !isNonEmptyString(value.objective) ||
    typeof value.success_criteria !== "string"
  ) {
    return undefined;
  }
  return {
    title: value.title,
    objective: value.objective,
    success_criteria: value.success_criteria,
  };
}

/**
 * Setup questions, skipping anything unusable rather than failing the card.
 *
 * A malformed step must not cost the reader the whole wizard: the remaining
 * questions are still answerable, and a card that renders nothing is worse
 * than a card that renders less. Returns `undefined` when nothing survives,
 * which falls back to the plain free-text question.
 */
function parseSetupQuestions(value: unknown): SetupQuestion[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }

  const questions: SetupQuestion[] = [];
  for (const entry of value) {
    if (!isRecord(entry) || !isNonEmptyString(entry.question)) {
      continue;
    }
    const options: SetupQuestionOption[] = [];
    if (Array.isArray(entry.options)) {
      for (const option of entry.options) {
        if (!isRecord(option) || !isNonEmptyString(option.label)) {
          continue;
        }
        options.push({
          id: isNonEmptyString(option.id) ? option.id : option.label,
          label: option.label,
          ...(isNonEmptyString(option.description)
            ? { description: option.description }
            : {}),
        });
      }
    }
    questions.push({
      id: isNonEmptyString(entry.id) ? entry.id : `q${questions.length + 1}`,
      question: entry.question,
      ...(isNonEmptyString(entry.why) ? { why: entry.why } : {}),
      ...(options.length ? { options } : {}),
      ...(isNonEmptyString(entry.recommended_option_id)
        ? { recommended_option_id: entry.recommended_option_id }
        : {}),
      ...(isNonEmptyString(entry.recommendation)
        ? { recommendation: entry.recommendation }
        : {}),
      grounded: entry.grounded === true,
    });
  }
  return questions.length ? questions : undefined;
}

export function parseHumanInputRequest(
  value: unknown,
): HumanInputRequest | null {
  if (!isRecord(value)) {
    return null;
  }
  if (
    value.version !== 1 ||
    value.kind !== "human_input_request" ||
    !isNonEmptyString(value.source) ||
    !isNonEmptyString(value.request_id) ||
    !isNonEmptyString(value.question) ||
    !isHumanInputMode(value.input_mode)
  ) {
    return null;
  }

  const options = parseOptions(value.options);
  if (value.options !== undefined && options === undefined) {
    return null;
  }
  const dbtlCycleSetup = parseDbtlCycleSetup(value.dbtl_cycle_setup);
  if (value.dbtl_cycle_setup !== undefined && dbtlCycleSetup === undefined) {
    return null;
  }
  const setupQuestions = parseSetupQuestions(value.setup_questions);
  if (
    (value.input_mode === "single_choice" ||
      value.input_mode === "choice_with_other") &&
    (!options || options.length === 0)
  ) {
    return null;
  }

  const context = value.context;
  if (
    context !== undefined &&
    context !== null &&
    typeof context !== "string"
  ) {
    return null;
  }

  return {
    version: 1,
    kind: "human_input_request",
    source: value.source,
    request_id: value.request_id,
    ...(readOptionalString(value.tool_call_id)
      ? { tool_call_id: readOptionalString(value.tool_call_id) }
      : {}),
    ...(readOptionalString(value.clarification_type)
      ? { clarification_type: readOptionalString(value.clarification_type) }
      : {}),
    ...(readOptionalString(value.title)
      ? { title: readOptionalString(value.title) }
      : {}),
    question: value.question,
    ...(context !== undefined ? { context } : {}),
    input_mode: value.input_mode,
    ...(options ? { options } : {}),
    ...(dbtlCycleSetup ? { dbtl_cycle_setup: dbtlCycleSetup } : {}),
    ...(setupQuestions ? { setup_questions: setupQuestions } : {}),
  };
}

export function parseHumanInputResponse(
  value: unknown,
): HumanInputResponse | null {
  if (!isRecord(value)) {
    return null;
  }
  if (
    value.version !== 1 ||
    value.kind !== "human_input_response" ||
    !isNonEmptyString(value.source) ||
    !isNonEmptyString(value.request_id) ||
    !isNonEmptyString(value.value)
  ) {
    return null;
  }

  if (value.response_kind === "option") {
    if (!isNonEmptyString(value.option_id)) {
      return null;
    }
    return {
      version: 1,
      kind: "human_input_response",
      source: value.source,
      request_id: value.request_id,
      response_kind: "option",
      option_id: value.option_id,
      value: value.value,
    };
  }

  if (value.response_kind === "text") {
    return {
      version: 1,
      kind: "human_input_response",
      source: value.source,
      request_id: value.request_id,
      response_kind: "text",
      value: value.value,
    };
  }

  return null;
}

export function extractHumanInputRequest(
  message: Message,
): HumanInputRequest | null {
  if (message.type !== "tool") {
    return null;
  }
  const artifact = Reflect.get(message, "artifact");
  if (!isRecord(artifact)) {
    return null;
  }
  return parseHumanInputRequest(artifact.human_input);
}

export function extractHumanInputResponse(
  message: Message,
): HumanInputResponse | null {
  if (message.type !== "human") {
    return null;
  }
  const additionalKwargs = message.additional_kwargs;
  if (!isRecord(additionalKwargs)) {
    return null;
  }
  return parseHumanInputResponse(additionalKwargs.human_input_response);
}

export function deriveHumanInputThreadState(
  messages: Message[],
  isVisibleMessage: (message: Message) => boolean = (message) =>
    message.additional_kwargs?.hide_from_ui !== true,
): HumanInputThreadState {
  const answeredResponses = new Map<string, HumanInputResponse>();
  const seenRequestIds = new Set<string>();
  const requestOrder: string[] = [];

  for (const message of messages) {
    if (isVisibleMessage(message)) {
      const request = extractHumanInputRequest(message);
      if (request) {
        seenRequestIds.add(request.request_id);
        requestOrder.push(request.request_id);
      }
    }

    const response = extractHumanInputResponse(message);
    if (
      response &&
      seenRequestIds.has(response.request_id) &&
      !answeredResponses.has(response.request_id)
    ) {
      answeredResponses.set(response.request_id, response);
    }
  }

  const latestOpenRequestId =
    [...requestOrder]
      .reverse()
      .find((requestId) => !answeredResponses.has(requestId)) ?? null;

  return { answeredResponses, latestOpenRequestId };
}

export function hasOpenHumanInputRequest(
  messages: Message[],
  isVisibleMessage?: (message: Message) => boolean,
) {
  return (
    deriveHumanInputThreadState(messages, isVisibleMessage)
      .latestOpenRequestId !== null
  );
}

export function createHumanInputOptionResponse(
  request: HumanInputRequest,
  option: HumanInputOption,
): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: request.source,
    request_id: request.request_id,
    response_kind: "option",
    option_id: option.id,
    value: option.value,
  };
}

export function createHumanInputTextResponse(
  request: HumanInputRequest,
  value: string,
): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: request.source,
    request_id: request.request_id,
    response_kind: "text",
    value,
  };
}

export function buildHumanInputResponseText(
  request: HumanInputRequest,
  response: HumanInputResponse,
) {
  return `For your clarification "${request.question}", my answer is: ${response.value}`;
}
