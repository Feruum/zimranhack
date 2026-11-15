# Preparation to the exam date

`exam-pacing-v1` adds a personal forecast to `plan.deadline` on the Today and plan
responses. No migration or new provider is required.

The forecast counts individual study days in the learner's IANA timezone,
excluding the exam day. A one-day budget override changes only today's capacity;
future capacity uses the profile's daily availability. Group meetings remain
separate from that individual study budget.

Remaining work uses published lesson durations, unfinished independent evidence,
pending retrieval checks before the exam, and uncompleted published full mocks.
One task can satisfy a retrieval check and an evidence requirement; those are not
counted twice. Completed lessons, demonstrated mastery and completed mock runs
reduce the appropriate remaining work. Missing fresh families are a visible
content gap rather than an invented assignment.

Weekly targets show topics, estimated minutes, checks and full mock windows. The
preview is bounded to twelve weeks; totals still cover the whole remaining
period. Mock targets require their authored uninterrupted duration. Average daily
pace, total shortfall, overloaded weeks and insufficient delayed-check windows
are shown explicitly. These are workload estimates, not predictions of passing.

With twenty-one days or less, or a total time shortfall, weak skills receive
priority while prerequisites remain ahead of dependent topics. Due retrieval
checks retain their existing priority; initial expert support policy still
limits the daily work. Neither a close deadline nor a larger budget lowers
grading, novel-family or seven-day retention requirements.

Today creates a new persisted plan version after a profile change, submitted
attempt, mastery revision, lesson completion, mock completion or a newly due
review. A manual budget override survives progress changes and is reset when
the profile is changed. Older plan versions and attempt history are preserved.

The frontend card is `components/ThinkTogether/ExamPacing.tsx`; it appears on
Today when an exam date is configured. Source changes must be included in the
next normal API/web build. No running services are restarted by the feature's
isolated tests.
