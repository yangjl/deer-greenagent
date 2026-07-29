import type { Message } from "@langchain/langgraph-sdk";

export type HumanInputMode =
  | "free_text"
  | "single_choice"
  | "choice_with_other"
  | "form";

export type HumanInputOption = {
  id: string;
  label: string;
  value: string;
  description?: string;
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

/**
 * One editable participant card on the design-meeting preflight request.
 *
 * Present only on `council_preflight` cards. The values are the roster
 * writer's suggestions — what runs if the person touches nothing — and the
 * card lets them edit the model, token budget, reasoning strength, and
 * instructions per participant. Edits ride back on the option reply as
 * `participants` and are re-validated server-side.
 */
export type CouncilParticipant = {
  id: string;
  role: string;
  role_label: string;
  agent_name: string;
  via_generalist: boolean;
  focus?: string;
  model: string;
  model_options: string[];
  max_tokens: number;
  max_tokens_min: number;
  max_tokens_max: number;
  /** False means usage is recorded after the run but never stops the seat. */
  token_limit_enforced?: boolean;
  reasoning: string;
  reasoning_options: string[];
  instructions: string;
};

export type CouncilParticipantEdits = {
  model?: string;
  max_tokens?: number;
  reasoning?: string;
  instructions?: string;
};

export type HumanInputFieldType =
  | "text"
  | "textarea"
  | "number"
  | "select"
  | "multi_select"
  | "checkbox"
  | "date";

export type HumanInputField = {
  name: string;
  label: string;
  type: HumanInputFieldType;
  required: boolean;
  placeholder?: string;
  options?: HumanInputOption[];
};

export type HumanInputFormValue = string | number | boolean | string[];

export type HumanInputRequest = {
  version: 1 | 2;
  kind: "human_input_request";
  source: "ask_clarification" | string;
  request_id: string;
  tool_call_id?: string;
  clarification_type?: string;
  design_feedback_surface_id?: string;
  title?: string;
  question: string;
  context?: string | null;
  input_mode: HumanInputMode;
  options?: HumanInputOption[];
  recommended_option_id?: string;
  dbtl_cycle_setup?: DbtlCycleSetup;
  setup_questions?: SetupQuestion[];
  fields?: HumanInputField[];
  council_participants?: CouncilParticipant[];
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
      // Participant edits from a design-meeting preflight card, keyed by
      // participant id. Optional and additive: the backend validates each
      // field and older backends ignore the key entirely.
      participants?: Record<string, CouncilParticipantEdits>;
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
    value === "choice_with_other" ||
    value === "form"
  );
}

// Field names that collide with JavaScript Object.prototype members. Form
// values live in a plain object keyed by field name, so these would resolve
// to inherited properties instead of user input.
const RESERVED_FIELD_NAMES = new Set([
  "__proto__",
  "constructor",
  "prototype",
  "toString",
  "toLocaleString",
  "valueOf",
  "hasOwnProperty",
  "isPrototypeOf",
  "propertyIsEnumerable",
  "__defineGetter__",
  "__defineSetter__",
  "__lookupGetter__",
  "__lookupSetter__",
]);

export function readHumanInputFormValue(
  values: Record<string, HumanInputFormValue>,
  name: string,
): HumanInputFormValue | undefined {
  return Object.prototype.hasOwnProperty.call(values, name)
    ? values[name]
    : undefined;
}

export function buildInitialHumanInputFormValues(
  fields: HumanInputField[],
): Record<string, HumanInputFormValue> {
  // Checkboxes are booleans that default to an explicit "no" — without the
  // seed an untouched checkbox would be indistinguishable from an unanswered
  // field and silently vanish from the submitted summary.
  const values: Record<string, HumanInputFormValue> = {};
  for (const field of fields) {
    if (field.type === "checkbox") {
      values[field.name] = false;
    }
  }
  return values;
}

function isHumanInputFieldType(value: unknown): value is HumanInputFieldType {
  return (
    value === "text" ||
    value === "textarea" ||
    value === "number" ||
    value === "select" ||
    value === "multi_select" ||
    value === "checkbox" ||
    value === "date"
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
  const seenIds = new Set<string>();
  const seenValues = new Set<string>();
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
      // An empty option value crashes Radix <SelectItem value="">; a
      // malformed/replayed artifact must fall back to plain text instead.
      !isNonEmptyString(optionValue) ||
      seenIds.has(id) ||
      seenValues.has(optionValue)
    ) {
      return undefined;
    }
    seenIds.add(id);
    seenValues.add(optionValue);
    options.push({
      id,
      label,
      value: optionValue,
      ...(isNonEmptyString(option.description)
        ? { description: option.description }
        : {}),
    });
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

function parseStringList(value: unknown): string[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.filter((entry): entry is string => isNonEmptyString(entry));
}

function toFiniteNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value)
    ? value
    : undefined;
}

/**
 * Participant cards, skipping anything unusable rather than failing the card.
 *
 * Same posture as `parseSetupQuestions`: one malformed participant must not
 * cost the reader the whole preflight — the depth options still work without
 * the editor. Returns `undefined` when nothing survives, which renders the
 * plain roster text instead.
 */
function parseCouncilParticipants(
  value: unknown,
): CouncilParticipant[] | undefined {
  if (!Array.isArray(value)) {
    return undefined;
  }

  const participants: CouncilParticipant[] = [];
  const seenIds = new Set<string>();
  for (const entry of value) {
    if (
      !isRecord(entry) ||
      !isNonEmptyString(entry.id) ||
      !isNonEmptyString(entry.agent_name) ||
      seenIds.has(entry.id)
    ) {
      continue;
    }
    const maxTokens = toFiniteNumber(entry.max_tokens);
    if (maxTokens === undefined) {
      continue;
    }
    seenIds.add(entry.id);
    participants.push({
      id: entry.id,
      role: isNonEmptyString(entry.role) ? entry.role : "position",
      role_label: isNonEmptyString(entry.role_label)
        ? entry.role_label
        : "Participant",
      agent_name: entry.agent_name,
      via_generalist: entry.via_generalist === true,
      ...(isNonEmptyString(entry.focus) ? { focus: entry.focus } : {}),
      model: isNonEmptyString(entry.model) ? entry.model : "",
      model_options: parseStringList(entry.model_options),
      max_tokens: maxTokens,
      max_tokens_min: toFiniteNumber(entry.max_tokens_min) ?? 1,
      max_tokens_max: toFiniteNumber(entry.max_tokens_max) ?? maxTokens,
      reasoning: isNonEmptyString(entry.reasoning) ? entry.reasoning : "",
      reasoning_options: parseStringList(entry.reasoning_options),
      instructions:
        typeof entry.instructions === "string" ? entry.instructions : "",
    });
  }
  return participants.length ? participants : undefined;
}

function parseParticipantEdits(
  value: unknown,
): Record<string, CouncilParticipantEdits> | undefined {
  if (!isRecord(value)) {
    return undefined;
  }
  const edits: Record<string, CouncilParticipantEdits> = {};
  for (const [id, raw] of Object.entries(value)) {
    if (!isNonEmptyString(id) || !isRecord(raw)) {
      continue;
    }
    const entry: CouncilParticipantEdits = {
      ...(isNonEmptyString(raw.model) ? { model: raw.model } : {}),
      ...(toFiniteNumber(raw.max_tokens) !== undefined
        ? { max_tokens: toFiniteNumber(raw.max_tokens) }
        : {}),
      ...(isNonEmptyString(raw.reasoning) ? { reasoning: raw.reasoning } : {}),
      ...(typeof raw.instructions === "string"
        ? { instructions: raw.instructions }
        : {}),
    };
    if (Object.keys(entry).length > 0) {
      edits[id] = entry;
    }
  }
  return Object.keys(edits).length > 0 ? edits : undefined;
}

/**
 * The edits a submitted preflight should carry: only fields that differ from
 * the card's own prefills, so an untouched card submits nothing and the
 * backend records the roster exactly as proposed.
 */
export function buildCouncilParticipantEdits(
  participants: CouncilParticipant[],
  values: Record<
    string,
    {
      model: string;
      maxTokens: string;
      reasoning: string;
      instructions: string;
    }
  >,
): Record<string, CouncilParticipantEdits> | undefined {
  const edits: Record<string, CouncilParticipantEdits> = {};
  for (const participant of participants) {
    const value = Object.prototype.hasOwnProperty.call(values, participant.id)
      ? values[participant.id]
      : undefined;
    if (!value) {
      continue;
    }
    const entry: CouncilParticipantEdits = {};
    if (value.model && value.model !== participant.model) {
      entry.model = value.model;
    }
    const tokens = Number.parseInt(value.maxTokens, 10);
    if (Number.isFinite(tokens) && tokens !== participant.max_tokens) {
      entry.max_tokens = tokens;
    }
    if (value.reasoning && value.reasoning !== participant.reasoning) {
      entry.reasoning = value.reasoning;
    }
    if (value.instructions.trim() !== participant.instructions.trim()) {
      entry.instructions = value.instructions.trim();
    }
    if (Object.keys(entry).length > 0) {
      edits[participant.id] = entry;
    }
  }
  return Object.keys(edits).length > 0 ? edits : undefined;
}

const COUNCIL_POSITION_LIMITS: Record<string, number> = {
  light: 1,
  medium: 2,
  heavy: 4,
};

/**
 * The participants a selected debate depth will actually dispatch.
 *
 * The preflight can show a medium roster while the owner chooses light. Keep
 * the first N independent positions and always preserve the red team and
 * chair, matching the backend's depth policy. Human-authored Design seats
 * nobody.
 */
export function participantsForCouncilDepth(
  participants: CouncilParticipant[],
  depth: string,
): CouncilParticipant[] {
  if (depth === "human_input") {
    return [];
  }
  const limit = COUNCIL_POSITION_LIMITS[depth];
  if (limit === undefined) {
    return participants;
  }
  let positions = 0;
  return participants.filter((participant) => {
    if (participant.role !== "position") {
      return true;
    }
    positions += 1;
    return positions <= limit;
  });
}

function parseFields(value: unknown): HumanInputField[] | undefined {
  if (value === undefined) {
    return undefined;
  }
  if (!Array.isArray(value)) {
    return undefined;
  }

  const fields: HumanInputField[] = [];
  const seenNames = new Set<string>();
  for (const field of value) {
    if (!isRecord(field)) {
      return undefined;
    }
    const name = field.name;
    if (typeof name === "string" && seenNames.has(name)) {
      return undefined;
    }
    if (typeof name === "string") {
      seenNames.add(name);
    }
    const label = field.label;
    const type = field.type;
    const required = field.required;
    if (
      !isNonEmptyString(name) ||
      RESERVED_FIELD_NAMES.has(name) ||
      !isNonEmptyString(label) ||
      !isHumanInputFieldType(type) ||
      (required !== undefined && typeof required !== "boolean")
    ) {
      return undefined;
    }
    const options = parseOptions(field.options);
    if (field.options !== undefined && options === undefined) {
      return undefined;
    }
    if (
      (type === "select" || type === "multi_select") &&
      (!options || options.length === 0)
    ) {
      return undefined;
    }
    fields.push({
      name,
      label,
      type,
      required: required === true,
      ...(readOptionalString(field.placeholder)
        ? { placeholder: readOptionalString(field.placeholder) }
        : {}),
      ...(options ? { options } : {}),
    });
  }
  return fields;
}

export function parseHumanInputRequest(
  value: unknown,
): HumanInputRequest | null {
  if (!isRecord(value)) {
    return null;
  }
  if (
    (value.version !== 1 && value.version !== 2) ||
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

  const fields = parseFields(value.fields);
  if (value.fields !== undefined && fields === undefined) {
    return null;
  }
  const councilParticipants = parseCouncilParticipants(
    value.council_participants,
  );
  if (value.input_mode === "form" && (!fields || fields.length === 0)) {
    return null;
  }
  // Version/mode binding: `form` is a v2 construct and v2 defines nothing
  // else — a mismatched pair is a malformed payload, not a variant.
  if ((value.input_mode === "form") !== (value.version === 2)) {
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
  const recommendedOptionId = isNonEmptyString(value.recommended_option_id)
    ? value.recommended_option_id
    : undefined;

  return {
    version: value.version,
    kind: "human_input_request",
    source: value.source,
    request_id: value.request_id,
    ...(readOptionalString(value.tool_call_id)
      ? { tool_call_id: readOptionalString(value.tool_call_id) }
      : {}),
    ...(readOptionalString(value.clarification_type)
      ? { clarification_type: readOptionalString(value.clarification_type) }
      : {}),
    ...(readOptionalString(value.design_feedback_surface_id)
      ? {
          design_feedback_surface_id: readOptionalString(
            value.design_feedback_surface_id,
          ),
        }
      : {}),
    ...(readOptionalString(value.title)
      ? { title: readOptionalString(value.title) }
      : {}),
    question: value.question,
    ...(context !== undefined ? { context } : {}),
    input_mode: value.input_mode,
    ...(options ? { options } : {}),
    ...(recommendedOptionId &&
    options?.some((option) => option.id === recommendedOptionId)
      ? { recommended_option_id: recommendedOptionId }
      : {}),
    ...(dbtlCycleSetup ? { dbtl_cycle_setup: dbtlCycleSetup } : {}),
    ...(setupQuestions ? { setup_questions: setupQuestions } : {}),
    ...(fields ? { fields } : {}),
    ...(councilParticipants
      ? { council_participants: councilParticipants }
      : {}),
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
    const participants = parseParticipantEdits(value.participants);
    return {
      version: 1,
      kind: "human_input_response",
      source: value.source,
      request_id: value.request_id,
      response_kind: "option",
      option_id: value.option_id,
      value: value.value,
      ...(participants ? { participants } : {}),
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

function extractPlainMessageText(message: Message): string {
  const content: unknown = message.content;
  if (typeof content === "string") {
    return content.trim();
  }
  if (Array.isArray(content)) {
    return content
      .map((part) =>
        isRecord(part) && part.type === "text" && typeof part.text === "string"
          ? part.text
          : "",
      )
      .join("")
      .trim();
  }
  return "";
}

export function deriveHumanInputThreadState(
  messages: Message[],
  isVisibleMessage: (message: Message) => boolean = (message) =>
    message.additional_kwargs?.hide_from_ui !== true,
): HumanInputThreadState {
  const answeredResponses = new Map<string, HumanInputResponse>();
  const seenRequests = new Map<string, HumanInputRequest>();
  const requestOrder: string[] = [];

  for (const message of messages) {
    if (isVisibleMessage(message)) {
      const request = extractHumanInputRequest(message);
      if (request) {
        seenRequests.set(request.request_id, request);
        requestOrder.push(request.request_id);
      }
    }

    const response = extractHumanInputResponse(message);
    if (
      response &&
      seenRequests.has(response.request_id) &&
      !answeredResponses.has(response.request_id)
    ) {
      answeredResponses.set(response.request_id, response);
      continue;
    }

    // Legacy-frontend fallback: a v1-only frontend renders a v2 request as
    // plain text and the user answers through the normal composer, so the
    // reply carries no human_input_response metadata. The reply closes only
    // the LATEST unanswered request — the one the user was presumably
    // answering. Nothing guarantees at most one outstanding request across
    // runs, and closing them all would silently swallow older decisions with
    // the same text; an older request left open simply becomes the active
    // card again.
    if (message.type === "human" && isVisibleMessage(message) && !response) {
      const latestUnansweredId = [...requestOrder]
        .reverse()
        .find((requestId) => !answeredResponses.has(requestId));
      const request =
        latestUnansweredId === undefined
          ? undefined
          : seenRequests.get(latestUnansweredId);
      if (latestUnansweredId !== undefined && request) {
        answeredResponses.set(latestUnansweredId, {
          version: 1,
          kind: "human_input_response",
          source: request.source,
          request_id: latestUnansweredId,
          response_kind: "text",
          value: extractPlainMessageText(message) || "-",
        });
      }
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
  const latestOpenRequestId = deriveHumanInputThreadState(
    messages,
    isVisibleMessage,
  ).latestOpenRequestId;
  if (latestOpenRequestId === null) return false;
  const request = [...messages]
    .reverse()
    .map(extractHumanInputRequest)
    .find((candidate) => candidate?.request_id === latestOpenRequestId);
  // A verified deck-backed Design request remains in durable thread state for
  // the supervisor, but the deck is now its input surface. It must not lock the
  // ordinary composer or render a duplicate card.
  return !request?.design_feedback_surface_id;
}

export function createHumanInputOptionResponse(
  request: HumanInputRequest,
  option: HumanInputOption,
  participants?: Record<string, CouncilParticipantEdits>,
): HumanInputResponse {
  return {
    version: 1,
    kind: "human_input_response",
    source: request.source,
    request_id: request.request_id,
    response_kind: "option",
    option_id: option.id,
    value: option.value,
    ...(participants && Object.keys(participants).length > 0
      ? { participants }
      : {}),
  };
}

function formatFormValue(value: HumanInputFormValue) {
  if (Array.isArray(value)) {
    return value.join(", ");
  }
  if (typeof value === "boolean") {
    return value ? "yes" : "no";
  }
  return String(value);
}

function isEmptyFormValue(value: HumanInputFormValue | undefined) {
  if (value === undefined) {
    return true;
  }
  if (typeof value === "string") {
    return value.trim().length === 0;
  }
  if (Array.isArray(value)) {
    return value.length === 0;
  }
  return false;
}

export function buildHumanInputFormSummary(
  request: HumanInputRequest,
  values: Record<string, HumanInputFormValue>,
) {
  const fields = request.fields ?? [];
  const parts: string[] = [];
  for (const field of fields) {
    const value = readHumanInputFormValue(values, field.name);
    if (isEmptyFormValue(value)) {
      continue;
    }
    parts.push(`${field.label}: ${formatFormValue(value!)}`);
  }
  return parts.join("; ");
}

export function buildHumanInputFormSubmissionValue(
  request: HumanInputRequest,
  values: Record<string, HumanInputFormValue>,
) {
  // The readable summary alone is ambiguous ("a: x; B: y" could come from
  // several field mappings), so the submitted value appends the full record
  // as one JSON block keyed by stable field names — labels/names may contain
  // the separators themselves, so only whole-record JSON is collision-free.
  const record: Record<string, HumanInputFormValue> = {};
  for (const field of request.fields ?? []) {
    const value = readHumanInputFormValue(values, field.name);
    if (isEmptyFormValue(value)) {
      continue;
    }
    record[field.name] = value!;
  }
  return `${buildHumanInputFormSummary(request, values)} [values: ${JSON.stringify(record)}]`;
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
