# Development Notes

## Important Implementation Details

### YouTube Music API Challenge

The biggest technical challenge is that YouTube Music doesn't have an official API. Here are the options:

#### Option 1: ytmusicapi (Recommended for Desktop/Local)
- Unofficial library that emulates browser requests
- Works great locally but has issues in Cloud Functions
- Requires browser headers and cookies

**Workaround for Cloud Functions:**
- Use YouTube Data API v3 to get liked videos
- Filter for music content (category ID 10)
- Less accurate than ytmusicapi but works in serverless environment

#### Option 2: YouTube Data API v3 (Currently Implemented)
- Official API from Google
- Can get "liked" videos but not specifically YouTube Music
- Need to filter results to identify music videos
- More reliable for Cloud Functions

#### Option 3: Hybrid Approach (Future Enhancement)
- Use ytmusicapi in a separate long-running service
- Cloud Functions trigger the service
- Service returns results to Firestore
- More complex but most accurate

### Current Implementation Notes

1. **OAuth Flow:**
   - Uses `start_oauth` function to initiate
   - User authorizes in popup window
   - `oauth_callback` function handles the redirect
   - Tokens stored in Firestore (should add encryption!)

2. **Playlist Intersection Logic:**
   - Currently a placeholder in `generate_playlist` function
   - Need to implement:
     ```python
     def calculate_intersection(all_songs_dict):
         # all_songs_dict = {user_id: [song_list]}
         # Convert to sets of song IDs
         song_sets = [set(song['video_id'] for song in songs) 
                      for songs in all_songs_dict.values()]
         # Find intersection
         intersection = set.intersection(*song_sets) if song_sets else set()
         return list(intersection)
     ```

3. **Security Concerns:**
   - Refresh tokens are stored in plaintext - should encrypt!
   - Consider using Google Cloud Secret Manager
   - Firestore rules are basic - may need refinement

4. **Rate Limiting:**
   - YouTube Data API has quotas (10,000 units/day free)
   - Each liked videos request costs ~1-3 units
   - For 4 users with 1000 liked songs each: ~10-15 units
   - Should be fine for small groups

## Known Issues & TODOs

### High Priority
- [ ] Implement actual intersection calculation
- [ ] Create YouTube playlist via API
- [ ] Add token encryption
- [ ] Better error handling and user feedback
- [ ] Add loading states for all async operations

### Medium Priority
- [ ] Implement proper ytmusicapi integration
- [ ] Add playlist refresh functionality
- [ ] Store generated playlists in Firestore
- [ ] Add member management (remove members, leave group)
- [ ] Add playlist preview before creation

### Low Priority
- [ ] Add analytics/logging
- [ ] Implement "Join Group" button in UI
- [ ] Add user profile page
- [ ] Export group data
- [ ] Dark mode

## Testing Checklist

Before deploying to production:

- [ ] Test OAuth flow with multiple users
- [ ] Test group creation
- [ ] Test joining groups
- [ ] Test playlist generation with 2+ users
- [ ] Verify Firestore rules work correctly
- [ ] Test on mobile devices
- [ ] Check all error states
- [ ] Verify redirect URIs are correct
- [ ] Test with users who have 0 liked songs
- [ ] Test with users who have 1000+ liked songs

## API Quotas & Costs

### YouTube Data API v3 (Free Tier)
- 10,000 units/day
- Costs per operation:
  - List (liked videos): 1 unit per request
  - Create playlist: 50 units
  - Add to playlist: 50 units
- Estimated usage for 4 users creating 1 playlist:
  - Fetch liked songs: 4 × 1 = 4 units
  - Create playlist: 50 units
  - Add songs: 50 units
  - **Total: ~104 units per playlist generation**
- Can generate ~96 playlists/day within free tier

### Firebase (Spark Plan - Free)
- Firestore: 50k reads, 20k writes/day
- Functions: 2M invocations/month
- Hosting: 10 GB storage, 360 MB/day bandwidth
- For 10 daily active users: well within limits

### When to Upgrade to Blaze Plan
- Functions: Need Blaze plan for external API calls (like YouTube API)
- Only pay for what you use beyond free tier
- For this app with small user base: likely $0-1/month

## Deployment Checklist

1. **Pre-Deploy:**
   - [ ] Update Firebase config in app.js
   - [ ] Set environment variables for OAuth credentials
   - [ ] Test locally with emulators
   - [ ] Remove emulator code from app.js
   - [ ] Update OAuth redirect URIs with production URLs

2. **Deploy:**
   - [ ] Deploy Firestore rules
   - [ ] Deploy Cloud Functions
   - [ ] Deploy Hosting

3. **Post-Deploy:**
   - [ ] Test production OAuth flow
   - [ ] Verify all functions are accessible
   - [ ] Check CORS configuration
   - [ ] Monitor function logs for errors

## Useful Commands

```bash
# View function logs
firebase functions:log

# View function config
firebase functions:config:get

# Set function config
firebase functions:config:set key="value"

# Delete function config
firebase functions:config:unset key

# Run emulators
firebase emulators:start

# Deploy specific service
firebase deploy --only hosting
firebase deploy --only functions
firebase deploy --only firestore:rules

# Open Firebase console
firebase open
```

## Resources

- [Firebase Documentation](https://firebase.google.com/docs)
- [YouTube Data API](https://developers.google.com/youtube/v3)
- [ytmusicapi Documentation](https://ytmusicapi.readthedocs.io/)
- [Firestore Security Rules](https://firebase.google.com/docs/firestore/security/get-started)

## Environment Variables Reference

Set via Firebase Functions config:

```bash
firebase functions:config:set \
  google.client_id="YOUR_OAUTH_CLIENT_ID" \
  google.client_secret="YOUR_OAUTH_CLIENT_SECRET"
```

Access in Python:
```python
os.environ.get('GOOGLE_CLIENT_ID')
os.environ.get('GOOGLE_CLIENT_SECRET')
```

## Notes for Future Platform Expansion

When adding Spotify/Apple Music:

1. Create separate OAuth flows for each platform
2. Store platform type in user document
3. Normalize song data structure:
   ```python
   {
       'platform': 'youtube_music' | 'spotify' | 'apple_music',
       'song_id': 'platform_specific_id',
       'title': 'Song Name',
       'artist': 'Artist Name',
       'isrc': 'International_Standard_Recording_Code'  # For matching across platforms
   }
   ```
4. Use ISRC codes to match songs across platforms
5. Create playlist on user's preferred platform

## Quick Reference: Firebase Project Setup

1. Create project at https://console.firebase.google.com
2. Enable Firestore, Authentication, Functions
3. Set up Google OAuth provider
4. Get Firebase config for web
5. Enable YouTube Data API in Google Cloud Console
6. Create OAuth credentials with redirect URIs
7. Set credentials in Functions config
8. Deploy!
