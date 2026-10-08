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
 * `none` is a version nobody proposed anything for; `inForce` has six expectations an agent
 * proposed, all in force and none answered (one in every tier, one of every kind); `mixed` is the
 * same with one rejected, with a reason; `carried` is a next version whose soak phase was renamed,
 * so the carried ones are in force and one needs a new phase and is not. The overrides are one in
 * force and the same one rejected. The ids are the database's own.
 */
const raw = JSON.parse(rawFixture) as {
  none: SignatureData;
  inForce: SignatureData;
  mixed: SignatureData;
  carried: SignatureData;
  overrideInForce: SignatureOverride;
  overrideRejected: SignatureOverride;
  threadId: number;
  setId: number;
  setVersionId: number;
  profileVersionId: number;
};

export const signatureNone: SignatureData = raw.none;
export const signatureInForce: SignatureData = raw.inForce;
export const signatureMixed: SignatureData = raw.mixed;
export const signatureCarried: SignatureData = raw.carried;
export const overrideInForce: SignatureOverride = raw.overrideInForce;
export const overrideRejected: SignatureOverride = raw.overrideRejected;
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
