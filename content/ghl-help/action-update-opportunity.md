Source: https://help.gohighlevel.com/support/solutions/articles/155000004753

# Update Opportunity action

Our summary of a HighLevel help article (checked 2026-10-06). Open the source link for the full steps.

## Summary

Describes the workflow action that changes an existing opportunity's name, value, pipeline, stage or status.

- It needs to know which opportunity: either the workflow was started by an opportunity trigger, or a Find Opportunity step comes first.
- Creating an opportunity earlier in the same workflow doesn't by itself tell this action which one to update.
- Moving a deal back to an earlier stage needs the Allow Opportunity to Move to Any Previous Stage option (off by default).
- It never creates opportunities and can't change the owner (use Assign User or Update Contact).
