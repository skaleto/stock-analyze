## ADDED Requirements

### Requirement: Fresh machine setup is documented
The repository SHALL document the minimum environment and commands needed to clone the project, create a Python environment, install dependencies, initialize runtime state, and run the simulation on another computer.

#### Scenario: Developer runs from a fresh checkout
- **WHEN** a developer follows the documented setup commands from a clean clone
- **THEN** the developer can install dependencies, initialize `data/`, run weekly or daily commands, and generate reports without using machine-specific paths from the original workstation

### Requirement: Simulation-only boundary is explicit
The documentation SHALL state that the system only performs simulated trading, does not connect to a broker, does not place real orders, and does not constitute investment advice.

#### Scenario: User reviews project purpose
- **WHEN** a user reads the runbook or README
- **THEN** the user can identify that outputs are research artifacts and not real trading instructions

### Requirement: Runtime outputs are described
The documentation SHALL list the generated runtime files and explain that `data/`, `reports/`, `logs/`, and `backups/` are local runtime artifacts excluded from normal Git commits.

#### Scenario: User checks generated files
- **WHEN** a run creates CSV, JSON, Markdown, HTML, or log files
- **THEN** the user can determine which files are generated state and which files are source-controlled code or documentation

### Requirement: Local and server commands are documented
The repository SHALL document local CLI commands and Linux systemd deployment commands for daily runs, weekly runs, dashboard serving, timer status, and log inspection.

#### Scenario: Operator starts the dashboard
- **WHEN** an operator follows the dashboard instructions
- **THEN** the dashboard is served on a loopback address and can be viewed through a local browser or an SSH tunnel

### Requirement: Verification steps are included
The runbook SHALL include smoke tests, dependency checks, compile checks, sensitive-information scans, and basic output checks.

#### Scenario: Operator validates a deployment
- **WHEN** an operator completes a deployment
- **THEN** the operator has a checklist to verify dependencies, services, generated reports, dashboard content, and data-source health
