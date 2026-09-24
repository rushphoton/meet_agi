/*
 * WHY THIS EXISTS
 * Gives the dashboard short names for the data shapes the backend defines.
 * Every shape here is only a pointer into the generated file
 * src/contract/schema.d.ts (made by scripts/export_openapi.py from the
 * backend's models) - nothing is typed by hand.
 *
 * FAILURE IT PREVENTS
 * The screens quietly disagreeing with the backend about what a meeting, an
 * alert or an event looks like. If the backend changes a shape, the generated
 * file changes, and the dashboard stops compiling until the screens catch up.
 */
import type { components, operations } from "@/contract/schema";

type S = components["schemas"];

export type MeetingRecord = S["MeetingRecord"];
export type MeetingListItem = S["MeetingListItem"];
export type CreateMeetingRequest = S["CreateMeetingRequest"];
export type TranscriptSegment = S["TranscriptSegment"];
export type Alert = S["Alert"];
export type SpokenAnswer = S["SpokenAnswer"];
export type ChatPost = S["ChatPost"];
export type FollowUp = S["FollowUp"];
export type FollowUpPatch = S["FollowUpPatch"];
export type MeetingSummary = S["MeetingSummary"];
export type Evidence = S["Evidence"];
export type Settings = S["Settings"];
export type SpeakerMapping = S["SpeakerMapping"];
export type DocumentInfo = S["DocumentInfo"];
export type Health = S["Health"];
export type Ok = S["Ok"];
export type WakeRequest = S["WakeRequest"];
export type MuteRequest = S["MuteRequest"];
export type BotStatusValue = S["BotStatus"]["status"];

/** The closed list of live-stream events, exactly as GET /api/contract/events declares it. */
export type MeetingEvent =
  operations["contract_events_api_contract_events_get"]["responses"][200]["content"]["application/json"];
export type MeetingEventType = MeetingEvent["type"];
