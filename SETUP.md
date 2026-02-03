# Intersect - Setup Guide

## Prerequisites
- Node.js (v18 or later)
- Python 3.9+
- Firebase CLI
- Google Cloud Project

## Step 1: Firebase Setup

### 1.1 Create Firebase Project
1. Go to https://console.firebase.google.com/
2. Click "Add project"
3. Name it "intersect" (or your preferred name)
4. Disable Google Analytics (optional)
5. Click "Create project"

### 1.2 Enable Firebase Services

**Firestore:**
1. Go to Build → Firestore Database
2. Click "Create database"
3. Start in **production mode** (we have security rules)
4. Choose your region (us-central1 recommended)

**Authentication:**
1. Go to Build → Authentication
2. Click "Get started"
3. Click "Sign-in method" tab
4. Enable "Google" provider
5. Add support email

**Functions:**
1. Go to Build → Functions
2. Click "Get started" (this enables the service)

### 1.3 Get Firebase Config
1. Go to Project Settings (gear icon)
2. Scroll to "Your apps"
3. Click web icon (</>) to add a web app
4. Register app name: "Intersect"
5. Copy the Firebase config object

## Step 2: Google Cloud Setup (YouTube API)

### 2.1 Enable YouTube Data API
1. Go to https://console.cloud.google.com/
2. Select your Firebase project (automatically created)
3. Navigate to "APIs & Services" → "Library"
4. Search for "YouTube Data API v3"
5. Click "Enable"

### 2.2 Create OAuth Credentials
1. Go to "APIs & Services" → "Credentials"
2. Click "Create Credentials" → "OAuth client ID"
3. If prompted, configure consent screen:
   - User Type: External
   - App name: Intersect
   - Support email: your email
   - Developer contact: your email
   - Scopes: Add `https://www.googleapis.com/auth/youtube.readonly` and `https://www.googleapis.com/auth/youtube`
   - Test users: Add your email
4. Create OAuth Client ID:
   - Application type: Web application
   - Name: Intersect Web Client
   - Authorized JavaScript origins: 
     - http://localhost:5000
     - https://YOUR_PROJECT_ID.web.app
   - Authorized redirect URIs:
     - http://localhost:5001/YOUR_PROJECT_ID/us-central1/oauth_callback
     - https://us-central1-YOUR_PROJECT_ID.cloudfunctions.net/oauth_callback
5. Save the **Client ID** and **Client Secret**

## Step 3: Local Project Setup

### 3.1 Install Firebase CLI
```bash
npm install -g firebase-tools
```

### 3.2 Login to Firebase
```bash
firebase login
```

### 3.3 Initialize Project
```bash
# Navigate to your project directory
cd intersect

# Initialize Firebase
firebase init

# Select:
# - Firestore
# - Functions
# - Hosting

# Choose "Use an existing project" and select your project

# For Firestore:
# - Use existing firestore.rules
# - Use existing firestore.indexes.json

# For Functions:
# - Language: Python
# - Source directory: functions
# - Install dependencies: Yes

# For Hosting:
# - Public directory: public
# - Single-page app: Yes
# - GitHub deploys: No
```

### 3.4 Configure Environment Variables

Set your Google OAuth credentials in Firebase Functions:

```bash
firebase functions:config:set \
  google.client_id="YOUR_CLIENT_ID" \
  google.client_secret="YOUR_CLIENT_SECRET"
```

### 3.5 Update Firebase Config in Frontend

Edit `public/app.js` and replace the Firebase config:

```javascript
const firebaseConfig = {
    apiKey: "YOUR_API_KEY",
    authDomain: "YOUR_PROJECT_ID.firebaseapp.com",
    projectId: "YOUR_PROJECT_ID",
    storageBucket: "YOUR_PROJECT_ID.appspot.com",
    messagingSenderId: "YOUR_MESSAGING_SENDER_ID",
    appId: "YOUR_APP_ID"
};
```

## Step 4: Deploy

### 4.1 Deploy Firestore Rules
```bash
firebase deploy --only firestore:rules
```

### 4.2 Deploy Functions
```bash
firebase deploy --only functions
```

### 4.3 Deploy Hosting
```bash
firebase deploy --only hosting
```

Or deploy everything at once:
```bash
firebase deploy
```

## Step 5: Test Locally (Optional)

### 5.1 Start Emulators
```bash
firebase emulators:start
```

### 5.2 Update app.js for Local Testing
Uncomment these lines in `public/app.js`:
```javascript
auth.useEmulator("http://localhost:9099");
functions.useEmulator("localhost", 5001);
```

### 5.3 Open in Browser
Navigate to http://localhost:5000

**Important:** Remember to comment out the emulator lines before deploying to production!

## Step 6: Update OAuth Redirect URIs

After deploying, get your Cloud Function URLs:
```bash
firebase functions:list
```

Update your Google Cloud OAuth credentials with the production redirect URI:
- https://us-central1-YOUR_PROJECT_ID.cloudfunctions.net/oauth_callback

## Troubleshooting

### Functions won't deploy
- Make sure you're on the Blaze (pay-as-you-go) plan for Cloud Functions
- Check Python version: `python --version` (needs 3.9+)

### OAuth not working
- Verify redirect URIs match exactly (no trailing slashes)
- Check that YouTube Data API is enabled
- Make sure consent screen is configured

### Firestore permission denied
- Deploy firestore rules: `firebase deploy --only firestore:rules`
- Check that user is authenticated

## Next Steps

1. Test the OAuth flow by signing in
2. Create a test group
3. Invite friends to join using the group ID
4. Generate your first intersection playlist!

## Known Limitations (Current MVP)

- YouTube Music API integration is basic (uses YouTube Data API)
- Playlist intersection logic needs refinement
- No automatic playlist updates (manual refresh only)
- Limited error handling

## Future Enhancements

- Better YouTube Music integration via ytmusicapi
- Scheduled playlist updates
- Support for other music platforms (Spotify, Apple Music)
- Advanced intersection options (n out of m users)
- Playlist preview before creation
