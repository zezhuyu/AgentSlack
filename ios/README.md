# Agent Slack for iOS

Native SwiftUI client for the Agent Slack HTTP API. It supports saved daemon
connections, per-server workspaces, people, direct messages, group chats,
streamed agent replies, Markdown, and structured JSON cards.

## Run

1. Start Agent Slack on the Mac and enable trusted LAN access.
2. Open `AgentSlack.xcodeproj` in Xcode.
3. Choose an iPhone or iPad simulator/device and run the `AgentSlack` scheme.
4. On first launch, paste **Copy API URL** from the Agent Slack macOS menu-bar
   app into the connection screen.

The URL is stored in `UserDefaults`. On every later launch, the app verifies
the saved backend before opening the workspace. If the backend is unreachable,
the connection screen returns with the saved URL prefilled so it can be retried
or modified.

The app accepts user-configured self-hosted `http://` URLs, including numeric
LAN addresses, through its App Transport Security exception. Agent Slack has no
built-in authentication, so do not connect it directly to the public internet.
Use HTTPS through a VPN or authenticated reverse proxy for remote access.

## Test

```bash
xcodebuild \
  -project ios/AgentSlack.xcodeproj \
  -scheme AgentSlack \
  -destination 'generic/platform=iOS Simulator' \
  CODE_SIGNING_ALLOWED=NO \
  build
```
