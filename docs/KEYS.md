# Account and access checklist

Prepare YouTube, Google and Slack access on your own computer. Keep credential files and secret values out of chat. Technical steps are in [Setup](SETUP.md). Allow roughly 45–75 minutes when accounts are ready, excluding organization approvals.

## 1. YouTube: approximately 5–10 minutes

- [ ] Sign in to the Google account managing the intended YouTube channel and confirm the channel identity.
- [ ] Complete [phone verification](https://www.youtube.com/verify) for long uploads when required.
- [ ] In Studio, check **Settings → Channel → Feature eligibility**, including intermediate features for long videos/custom thumbnails and any additional eligibility required for chapters. See [YouTube's feature guidance](https://support.google.com/youtube/answer/9890437?co=GENIE.Platform%3DDesktop&hl=en).
- [ ] Create the intended playlist, choose visibility and keep its full URL and `list=` ID.

Reel requests **unlisted** uploads. Anyone with a resulting link may watch the video.

## 2. Google Cloud: approximately 20–30 minutes

- [ ] Create/select the Reel project in [Google Cloud Console](https://console.cloud.google.com/) and record its Project ID.
- [ ] Enable YouTube Data API v3 and optional Google Classroom API.
- [ ] Configure app branding and support/developer contact emails.
- [ ] Configure the appropriate audience and test users, following organization policy.
- [ ] Add the required scopes listed in [Setup](SETUP.md).
- [ ] Create a Desktop OAuth client and save the downloaded JSON locally as `secrets/client_secret.json` in the repository. Avoid accidental double `.json` extensions.
- [ ] Perform explicit local authorization for each intended service. Confirm only that the file is in place; do not share its contents.

### Audit or quota request, when required

Keep the [YouTube API audit/quota request form](https://support.google.com/youtube/contact/yt_api_form?hl=en) available. A possible description, adjusted to the actual installation, is:

> We use the YouTube Data API to upload recordings of our classes to our own channel as unlisted videos, organize them in class playlists, and share links with enrolled students through Slack and Google Classroom. Publishing uses the channel owner's OAuth consent, after a privacy check of the recording.

Report actual usage and implemented features only. Historical API restrictions may change; consult the [revision history](https://developers.google.com/youtube/data/revision_history) and [quota/compliance guidance](https://developers.google.com/youtube/data/guides/quota_and_compliance_audits) before treating an audit as mandatory for a particular deployment.

## 3. Slack: approximately 10–20 minutes

- [ ] Create a Reel app in the intended workspace through [Slack Apps](https://api.slack.com/apps).
- [ ] Add `chat:write`, install the app and obtain workspace approval if needed.
- [ ] Put the Bot User OAuth Token into the existing `.env` `SLACK_BOT_TOKEN` field locally, preserving other settings.
- [ ] Invite the correct bot into every group/operator channel with `/invite @Reel`, including private channels.
- [ ] Record each Channel ID or channel link and distinguish group destinations from the operator destination.

Reinstall after scope changes. See [Slack setup](https://docs.slack.dev/app-management/quickstart-app-settings/) and [chat:write](https://docs.slack.dev/reference/scopes/chat.write/).

## 4. Classroom: approximately 2–5 minutes, optional

- [ ] Open the intended course using the account Reel will authorize.
- [ ] Confirm that account is a teacher, not merely a student.
- [ ] Record the course link and group mapping. The join code is not the API course ID.

## 5. Information safe to provide to the maintainer

Channel URLs, feature-eligibility status, group names, playlist IDs, Cloud Project ID, enabled API names, workspace/app names, Slack channel IDs, course links and outstanding approval steps can be supplied without sharing credentials.

**Never share:** client JSON contents, OAuth token files, `.env` contents, bot tokens, passwords, SMS/OTP codes, recovery codes or screenshots containing secrets. Confirmation that a file/token is configured locally is sufficient.

## 6. Troubleshooting

| Situation | Action |
| --- | --- |
| Long YouTube uploads unavailable | Verify the correct channel and its feature eligibility. |
| Slack requires approval | Request it from the workspace administrator; do not recreate the app repeatedly. |
| Google flags an unverified app | Verify the application's identity and account ownership, then follow the organization's approved authorization process. |
| Google blocks access/test user | Check the intended email and Workspace restrictions. |
| Google asks to authorize again after a week | Review External/Testing token lifetime and production requirements in Setup. |
| Classroom announcement fails | Check teacher role, numeric course ID and API policy. |
| Slack cannot post | Check installation, scope and membership of the exact channel. |
