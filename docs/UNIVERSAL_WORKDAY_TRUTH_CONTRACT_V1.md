# Universal Workday Truth Contract V1

Status: active design/implementation contract  
Scope: Hunter Universal Brain and every provider consumer, including Workday.

## Core rule

Provider mechanics may never become a second source of candidate truth.

Hunter owns canonical meaning, provenance, sensitivity, and answer policy. Apply observes a field, supplies structural evidence, and executes only the resolved intent.

## Normal facts

Normal facts may come from confirmed Candidate Truth or an explicitly approved answer source. Examples:

- legal name parts;
- email;
- address;
- phone number;
- phone device type when explicitly configured;
- phone country code when explicitly configured;
- phone extension only when a distinct extension fact exists;
- employment and education facts;
- websites;
- languages when explicitly present.

Missing optional facts remain missing. A provider default is not automatically Candidate Truth.

## Structured legal name

Preferred resolution order:

1. confirmed contact.first_name / contact.middle_name / contact.last_name;
2. conservative projection from confirmed contact.full_name for legacy profiles only;
3. NEEDS_INPUT when neither source exists.

Never compose a middle name into first name merely because a provider parser produced a duplicated full-name representation.

## Segmented phone

The following are different concepts:

- application.phone_device_type;
- contact.phone_country_code;
- contact.phone;
- contact.phone_extension.

Hard invariants:

- contact.phone cannot satisfy contact.phone_extension;
- contact.phone cannot satisfy contact.phone_country_code;
- phone extension has no derivation from the main phone digits;
- absent extension is a valid optional blank;
- formatting transforms may change punctuation/spacing but never digits.

## Eligibility and age threshold

work.location_eligibility and eligibility.age_18_plus are PROTECTED.

Rules:

- do not infer work-location eligibility merely from address, citizenship, school, or resume;
- do not infer age threshold from name, graduation year, appearance, or unrelated dates;
- exact date of birth is not required to answer an age-threshold question when the user has explicitly confirmed the threshold fact;
- protected values remain reference/policy based where the secure architecture requires it.

## Voluntary self-identification

SELF_ID includes:

- gender;
- race/ethnicity;
- Hispanic/Latino identity;
- veteran status;
- disability status.

Rules:

- never infer from name, language, nationality, address, photo, religion, resume, employer, or education;
- never promote a normal fact into a SELF_ID answer;
- never serialize protected/self-ID plaintext into routine browser diagnostic logs;
- user-selected “decline/do not want to answer” is a valid explicit self-ID choice when the provider offers it.

The ordinary structural fields on a self-identification form are different:

- self_id.form_name can resolve from confirmed legal full name;
- self_id.form_date can be supplied by an execution-date policy;
- employment.employee_id is POST_OFFER/optional and stays blank for an applicant unless explicitly known.

## Legal attestation

legal.attestation is not a generic boolean preference.

An answer may be resolved only from an execution mandate that is bound to the exact attestation/terms identity. The mandate must cover the visible label and terms/document fingerprint. This preserves the single-approval architecture without adding a second approval prompt at execution time.

## Repeated entities

Employment and education resolution must be entity scoped.

Examples:

- experience.0.title
- experience.0.employer
- experience.1.title
- education.0.institution
- education.1.institution

Browser repeatGroupId + repeatIndex may select which canonical entity is relevant. A generic semantic match alone cannot move a fact from one row into another.

## Browser semantic sensitivity floor

Apply semantic classification can make a field stricter, never less strict.

Minimum floors:

- WORK_AUTHORIZATION_CURRENT -> PROTECTED
- WORK_LOCATION_ELIGIBILITY -> PROTECTED
- AGE_18_PLUS -> PROTECTED
- SPONSORSHIP_CURRENT / SPONSORSHIP_FUTURE -> PROTECTED
- HISPANIC_LATINO -> SELF_ID
- GENDER / RACE_ETHNICITY / VETERAN / DISABILITY -> SELF_ID
- EMPLOYEE_ID -> POST_OFFER

If the ontology and browser disagree, use the stricter sensitivity.

## Review reconciliation

Review verification is a consumer of truth, not a producer.

It may verify:

- canonical name composition;
- phone digits and absence/presence of extension;
- address;
- repeated entity membership;
- exact resume artifact SHA;
- existence of protected provenance;
- attestation binding.

It may not learn a new canonical fact merely because Workday displayed it.

## Acceptance criteria

The Universal Brain side is ready only when tests prove:

- segmented phone concepts cannot alias each other;
- structured name parts outrank legacy full-name projection;
- age and work-location eligibility stay protected;
- Hispanic/Latino stays self-ID;
- employee ID stays post-offer;
- legal attestation never defaults to autofill;
- browser semantic hints cannot downgrade sensitivity;
- repeated rows remain isolated;
- unknown optional facts remain unresolved/blank rather than guessed.
