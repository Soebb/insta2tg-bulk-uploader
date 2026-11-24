package gogram

import (
	"encoding/json"
	"fmt"
	"log"
	"os"
	"path/filepath"
	"strings"
	"sync"

	"github.com/amarnathcjd/gogram/telegram"
)

var (
	mu sync.Mutex
	client *telegram.Client
	logFile *os.File
)

// Start connects and authenticates the MTProto bot. It is safe to call once
// for each downloader run. The session is stored in the app-private files dir.
func Start(filesDir, settingsJSON string) string {
	mu.Lock()
	defer mu.Unlock()

	if client != nil {
		return "Gogram client is already started"
	}

	var settings map[string]interface{}
	if err := json.Unmarshal([]byte(settingsJSON), &settings); err != nil {
		return writeError("Invalid Gogram settings: %v", err)
	}
	apiID, ok := settings["API_ID"]
	if !ok {
		return writeError("API_ID is missing")
	}
	var appID int32
	switch value := apiID.(type) {
	case float64:
		appID = int32(value)
	case string:
		var parsed int
		if _, err := fmt.Sscanf(value, "%d", &parsed); err != nil {
			return writeError("Invalid API_ID: %v", err)
		}
		appID = int32(parsed)
	default:
		return "Invalid API_ID type"
	}
	appHash := stringSetting(settings, "API_HASH")
	botToken := stringSetting(settings, "BOT_TOKEN")
	if appID <= 0 || appHash == "" || botToken == "" {
		return "API_ID, API_HASH and BOT_TOKEN are required"
	}

	sessionDir := filepath.Join(filesDir, "gogram")
	if err := os.MkdirAll(sessionDir, 0700); err != nil {
		return writeError("Could not create Gogram session directory: %v", err)
	}
	file, err := os.OpenFile(filepath.Join(filesDir, "downloader.log"), os.O_CREATE|os.O_APPEND|os.O_WRONLY, 0600)
	if err == nil {
		logFile = file
		log.SetOutput(file)
		log.SetFlags(log.Ldate | log.Ltime)
	}

	log.Printf("Initializing Gogram MTProto client.")
	created, err := telegram.NewClient(telegram.ClientConfig{
		AppID: appID,
		AppHash: appHash,
		Session: filepath.Join(sessionDir, "instagram_bulk_bot.session"),
	})
	if err != nil {
		return writeError("Gogram client creation failed: %v", err)
	}
	if err = created.Connect(); err != nil {
		_ = created.Terminate()
		return writeError("Gogram MTProto connection failed: %v", err)
	}
	if err = created.LoginBot(botToken); err != nil {
		_ = created.Terminate()
		return writeError("Gogram bot authorization failed: %v", err)
	}
	client = created
	log.Printf("Gogram MTProto client initialized successfully.")
	return "Gogram client ready"
}

// SendMedia sends one local file. Gogram detects photo/video from the file;
// forceDocument is used for SQLite checkpoint backups.
func SendMedia(path, caption, chatID string, forceDocument bool) string {
	mu.Lock()
	current := client
	mu.Unlock()
	if current == nil {
		return "Gogram client is not initialized"
	}
	options := &telegram.MediaOptions{
		Caption: caption,
		ForceDocument: forceDocument,
		Progress: progressLogger(filepath.Base(path)),
	}
	if _, err := current.SendMedia(chatID, path, options); err != nil {
		return writeError("Gogram upload failed for %s: %v", filepath.Base(path), err)
	}
	log.Printf("Gogram MTProto upload completed: %s", filepath.Base(path))
	return "OK"
}

// SendAlbum sends between 2 and 10 local files as one Telegram album.
func SendAlbum(pathsJSON, caption, chatID string) string {
	mu.Lock()
	current := client
	mu.Unlock()
	if current == nil {
		return "Gogram client is not initialized"
	}
	var paths []string
	if err := json.Unmarshal([]byte(pathsJSON), &paths); err != nil {
		return writeError("Invalid album file list: %v", err)
	}
	if len(paths) < 2 || len(paths) > 10 {
		return "Telegram albums must contain between 2 and 10 media items"
	}
	options := &telegram.MediaOptions{
		Caption: caption,
		Progress: progressLogger("album"),
	}
	if _, err := current.SendAlbum(chatID, paths, options); err != nil {
		return writeError("Gogram album upload failed: %v", err)
	}
	log.Printf("Gogram MTProto album upload completed (%d files).", len(paths))
	return "OK"
}

// Close releases the MTProto connection and session resources.
func Close() string {
	mu.Lock()
	current := client
	client = nil
	file := logFile
	logFile = nil
	mu.Unlock()
	if current != nil {
		if err := current.Terminate(); err != nil {
			return writeError("Gogram shutdown warning: %v", err)
		}
	}
	if file != nil {
		_ = file.Sync()
		_ = file.Close()
	}
	return "Gogram client closed"
}

func progressLogger(label string) func(int, int) {
	var progressMu sync.Mutex
	lastPercent := -5
	return func(totalBytes, currentBytes int) {
		if totalBytes <= 0 {
			return
		}
		percent := currentBytes * 100 / totalBytes
		progressMu.Lock()
		defer progressMu.Unlock()
		if percent < lastPercent+5 && currentBytes < totalBytes {
			return
		}
		lastPercent = percent
		log.Printf("Gogram MTProto upload %s: %d%% (%.1f/%.1f MB).",
			label, percent, float64(currentBytes)/1048576, float64(totalBytes)/1048576)
	}
}

func stringSetting(settings map[string]interface{}, key string) string {
	value, _ := settings[key].(string)
	return strings.TrimSpace(value)
}

func writeError(format string, args ...interface{}) string {
	message := fmt.Sprintf(format, args...)
	log.Printf("ERROR: %s", message)
	return message
}
