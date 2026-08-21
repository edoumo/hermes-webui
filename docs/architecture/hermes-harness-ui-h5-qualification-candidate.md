# Hermes Harness UI H5 qualification candidate

Status: `H5_UI_CODE_READY_FOR_INTEGRATED_QUALIFICATION`

Branch: `experimental/hermes-harness-ui-task-orchestration`

H4 parent: `601f6049f1d81d2e536e341f2e9fb1e7726ed09d`

The H5 Harness candidate adds a session-level operational DAG while preserving qualified H3/H4 ownership boundaries:

- H3 still owns auth, sessions and the single EventSource;
- H4 still owns worker cancel/retry and operations summary;
- H5 adds task graph/edit/dependency/dispatch/recovery routes through the same-origin BFF;
- graph is bounded to 100 tasks and rendered without a framework dependency;
- task cards expose assignment, edit, dependency add/remove, READY dispatch and failed-task recovery;
- task state is never updated optimistically in the browser;
- H5 recovery reuses the durable task message while backend creates a fresh activation;
- graph is promoted to session-level workspace and remains available without selecting a worker;
- no Bearer/API key, direct SQLite access, second EventSource or localStorage transcript is introduced.

The authoring environment cannot execute the repository test suite because it cannot resolve `github.com`; no integrated browser PASS is claimed. Contract tests are committed and the next gate is an isolated real-runtime/browser qualification.

No PR, merge or principal runtime mutation is authorized.
