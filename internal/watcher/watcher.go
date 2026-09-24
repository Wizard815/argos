// Package configwatcher provides a simple cross-platform file watcher based
// on os.Stat polling. It works correctly inside Docker containers where the
// config file is bind-mounted as an individual file, and for k8s ConfigMap
// projections (which present the file as a symlink to an atomically swapped
// target) — both cases where inotify-based watchers are unreliable.
package configwatcher

import (
	"context"
	"errors"
	"io/fs"
	"log"
	"os"
	"time"
)

const DefaultInterval = 2 * time.Second

type Watcher struct {
	Path     string
	Interval time.Duration
	OnChange func()
}

type snapshot struct {
	exists  bool
	modTime time.Time
	size    int64
}

// Run blocks until ctx is canceled. It polls Path on Interval and invokes
// OnChange whenever the file's modification time or size changes, or when
// the file reappears after being missing. The baseline poll establishes
// initial state and does not fire OnChange.
func (w *Watcher) Run(ctx context.Context) {
	interval := w.Interval
	if interval <= 0 {
		interval = DefaultInterval
	}

	var lastErrMsg string
	prev := stat(w.Path, &lastErrMsg)

	ticker := time.NewTicker(interval)
	defer ticker.Stop()

	for {
		select {
		case <-ctx.Done():
			return
		case <-ticker.C:
			cur := stat(w.Path, &lastErrMsg)
			if changed(prev, cur) && w.OnChange != nil {
				w.OnChange()
			}
			prev = cur
		}
	}
}

// stat calls os.Stat and logs at most once per distinct, persistent error
// (e.g. ESTALE from a bind-mount/NFS handle outliving the underlying file —
// os.Stat does a fresh syscall every call, so an identical error repeating
// forever means the mount itself is broken, not a bug this process can fix
// by retrying) plus once on recovery, instead of flooding the log every
// poll interval for as long as the underlying issue persists. A plain
// "file does not exist" is the expected steady state while a config is
// mid-write and is never logged, matching the previous behavior.
func stat(path string, lastErrMsg *string) snapshot {
	fi, err := os.Stat(path)
	if err == nil {
		if *lastErrMsg != "" {
			log.Printf("configwatcher: stat %s: recovered", path)
			*lastErrMsg = ""
		}
		return snapshot{
			exists:  true,
			modTime: fi.ModTime(),
			size:    fi.Size(),
		}
	}
	if errors.Is(err, fs.ErrNotExist) {
		*lastErrMsg = ""
		return snapshot{}
	}
	if msg := err.Error(); msg != *lastErrMsg {
		log.Printf("configwatcher: stat %s: %v", path, err)
		*lastErrMsg = msg
	}
	return snapshot{}
}

func changed(prev, cur snapshot) bool {
	// Present → missing: stay quiet (likely a transient rename-style write).
	// Missing → present: fire so we reload as soon as the file comes back.
	if !cur.exists {
		return false
	}
	if !prev.exists {
		return true
	}
	return !prev.modTime.Equal(cur.modTime) || prev.size != cur.size
}
