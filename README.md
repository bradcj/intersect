# Intersect 🎵

**Where music tastes intersect**

Intersect helps you discover music you all love by creating playlists from songs that everyone in your group has liked on YouTube Music.

## Features

- 🔐 Secure Google OAuth authentication
- 👥 Create and join music groups with friends and family
- ⊕ Generate playlists from the intersection of everyone's liked songs
- 🎶 Works with YouTube Music (more platforms coming soon)
- 🆓 Completely free to use

## How It Works

1. **Sign in** with your Google account
2. **Connect** your YouTube Music account
3. **Create a group** or join an existing one
4. **Invite friends** by sharing the group ID
5. **Generate** a playlist with songs you all love!

## Technology Stack

- **Frontend:** Vanilla JavaScript, HTML, CSS
- **Backend:** Firebase Cloud Functions (Python)
- **Database:** Cloud Firestore
- **Authentication:** Firebase Auth + Google OAuth
- **Hosting:** Firebase Hosting
- **APIs:** YouTube Data API v3

## Getting Started

See [SETUP.md](SETUP.md) for detailed setup instructions.

### Quick Start

```bash
# Install dependencies
npm install -g firebase-tools

# Login to Firebase
firebase login

# Deploy
firebase deploy
```

## Project Structure

```
intersect/
├── functions/
│   ├── main.py              # Cloud Functions (backend logic)
│   └── requirements.txt     # Python dependencies
├── public/
│   ├── index.html          # Frontend UI
│   ├── app.js              # Frontend logic
│   └── styles.css          # Styling
├── firestore.rules         # Database security rules
├── firebase.json           # Firebase configuration
├── SETUP.md               # Setup instructions
└── README.md              # This file
```

## Current Status

This is an MVP (Minimum Viable Product) with core functionality working. Currently supports:
- ✅ User authentication
- ✅ YouTube Music OAuth
- ✅ Group creation and management
- ✅ Basic playlist generation
- ⏳ Advanced intersection logic (in progress)
- ⏳ Automatic playlist updates (planned)

## Future Roadmap

- [ ] Improve YouTube Music integration with ytmusicapi
- [ ] Add support for Spotify
- [ ] Add support for Apple Music
- [ ] Implement n-out-of-m intersection (e.g., songs liked by 2+ people)
- [ ] Scheduled playlist updates
- [ ] Playlist preview before creation
- [ ] Export to different platforms
- [ ] Analytics and statistics

## Contributing

This is currently a personal project for friends and family. If you'd like to build something similar, feel free to fork!

## Privacy

- We only access your YouTube Music liked songs (read-only)
- OAuth tokens are stored securely in Firestore with encryption
- We don't share your data with third parties
- You can revoke access anytime via your Google account settings

## Support

For issues or questions, please open a GitHub issue.

---

Built with ❤️ using Firebase
