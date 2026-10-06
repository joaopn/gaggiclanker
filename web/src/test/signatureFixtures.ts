import type {
  SignatureData,
  SignatureExpectation,
  SignatureOverride,
  SignatureSetUsing,
} from "@/api/types";
import rawFixture from "@/test/fixtures/signature.json?raw";

/**
 * The signature routes' documents, generated from the real pipeline.
 *
 * Built by `scripts/build_web_shot_fields_fixture.py`, which stores the constructed lever
 * profile, proposes its signature the way an agent does and answers it the way the routes do.
 * `none` is a version nobody proposed anything for; `proposed` has four expectations waiting
 * (one in every tier, one of every kind but a warning); `mixed` has one of each answer;
 * `confirmed` has all four confirmed; `carried` is a next version whose soak phase was renamed,
 * so one expectation needs a new phase. The ids are the database's own.
 */
const raw = JSON.parse(rawFixture) as {
  none: SignatureData;
  proposed: SignatureData;
  mixed: SignatureData;
  confirmed: SignatureData;
  carried: SignatureData;
  overrideWaiting: SignatureOverride;
  overrideConfirmed: SignatureOverride;
  threadId: number;
  setId: number;
  setVersionId: number;
  profileVersionId: number;
};

export const signatureNone: SignatureData = raw.none;
export const signatureProposed: SignatureData = raw.proposed;
export const signatureMixed: SignatureData = raw.mixed;
export const signatureConfirmed: SignatureData = raw.confirmed;
export const signatureCarried: SignatureData = raw.carried;
export const overrideWaiting: SignatureOverride = raw.overrideWaiting;
export const overrideConfirmed: SignatureOverride = raw.overrideConfirmed;
export const SIGNATURE_THREAD_ID = raw.threadId;
export const SIGNATURE_SET_ID = raw.setId;
export const SIGNATURE_SET_VERSION_ID = raw.setVersionId;
export const SIGNATURE_PROFILE_VERSION_ID = raw.profileVersionId;

/** One expectation of a served document, by its phase and kind. */
export function expectationOf(
  data: SignatureData,
  phase: string | null,
  kind: SignatureExpectation["kind"],
): SignatureExpectation {
  const found = data.expectations.find((e) => e.phase === phase && e.kind === kind);
  if (!found) throw new Error(`no ${kind} expectation for ${phase}`);
  return found;
}

export const setsUsing = (data: SignatureData): SignatureSetUsing[] => data.sets;
