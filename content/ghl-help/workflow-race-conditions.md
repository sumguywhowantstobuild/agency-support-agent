Source: https://help.gohighlevel.com/support/solutions/articles/48001218068-troubleshooting-workflow-executions-race-conditions

# Workflow race conditions

Our summary of a HighLevel help article (checked 2026-10-06). Open the source link for the full steps.

## Summary

Explains what happens when two updates to the same contact land in the same second.

- An action can show as executed in the logs without its change actually sticking (for example a tag that never appears).
- Typical cause: adding a contact to a workflow and changing them (such as tagging) at the same moment.
- HighLevel's recommended fix is a one-minute Wait step between the dependent actions.
