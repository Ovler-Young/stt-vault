package state

import "sync"

type Entry struct {
	Hash     string
	Response []byte
}

type BeginResult uint8

const (
	Owner BeginResult = iota
	Replay
	Pending
	Mismatch
	Cancelled
)

type pendingEntry struct {
	hash string
	done chan struct{}
}

type Store struct {
	mu         sync.Mutex
	replay     map[string]Entry
	cancelled  map[string]bool
	active     string
	activeDone chan struct{}
	pending    map[string]pendingEntry
}

func New() *Store {
	return &Store{replay: map[string]Entry{}, cancelled: map[string]bool{}, pending: map[string]pendingEntry{}}
}
func (s *Store) Begin(key, hash string) (Entry, BeginResult, chan struct{}) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e, ok := s.replay[key]; ok {
		if e.Hash != hash {
			return Entry{}, Mismatch, nil
		}
		return e, Replay, nil
	}
	if s.cancelled[key] {
		return Entry{}, Cancelled, nil
	}
	if pending, ok := s.pending[key]; ok {
		if pending.hash != hash {
			return Entry{}, Mismatch, nil
		}
		return Entry{}, Pending, pending.done
	}
	done := make(chan struct{})
	s.pending[key] = pendingEntry{hash: hash, done: done}
	return Entry{}, Owner, done
}
func (s *Store) Activate(key string, done chan struct{}) bool {
	s.mu.Lock()
	defer s.mu.Unlock()
	if s.cancelled[key] {
		return false
	}
	s.active = key
	s.activeDone = done
	return true
}
func (s *Store) Finish(key, hash string, response []byte, done chan struct{}, completed bool) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if completed && !s.cancelled[key] {
		s.replay[key] = Entry{hash, response}
	}
	if s.active == key {
		s.active = ""
		s.activeDone = nil
	}
	delete(s.pending, key)
	close(done)
}

func (s *Store) Resolve(key, hash string) (Entry, BeginResult) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if entry, ok := s.replay[key]; ok {
		if entry.Hash != hash {
			return Entry{}, Mismatch
		}
		return entry, Replay
	}
	if s.cancelled[key] {
		return Entry{}, Cancelled
	}
	return Entry{}, Owner
}

func (s *Store) Cancel(key string) (active bool, done chan struct{}) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if _, ok := s.replay[key]; ok {
		return false, nil
	}
	s.cancelled[key] = true
	if s.active == key {
		return true, s.activeDone
	}
	return false, s.pending[key].done
}
func (s *Store) Cancelled(key string) bool { s.mu.Lock(); defer s.mu.Unlock(); return s.cancelled[key] }
