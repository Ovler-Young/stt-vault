package state

import "sync"

type Entry struct {
	Hash     string
	Response []byte
}
type Store struct {
	mu         sync.Mutex
	replay     map[string]Entry
	cancelled  map[string]bool
	active     string
	activeDone chan struct{}
	queued     map[string]chan struct{}
}

func New() *Store {
	return &Store{replay: map[string]Entry{}, cancelled: map[string]bool{}, queued: map[string]chan struct{}{}}
}
func (s *Store) Begin(key string) (Entry, bool, bool, chan struct{}) {
	s.mu.Lock()
	defer s.mu.Unlock()
	if e, ok := s.replay[key]; ok {
		return e, true, false, nil
	}
	if s.cancelled[key] {
		return Entry{}, false, true, nil
	}
	done := make(chan struct{})
	s.queued[key] = done
	return Entry{}, false, false, done
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
	delete(s.queued, key)
	close(done)
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
	return false, s.queued[key]
}
func (s *Store) Cancelled(key string) bool { s.mu.Lock(); defer s.mu.Unlock(); return s.cancelled[key] }
