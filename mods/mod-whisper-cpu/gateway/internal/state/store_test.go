package state

import "testing"

func TestCancellationAndReplayState(t *testing.T) {
	s := New()
	_, result, done := s.Begin("key", "hash")
	if result != Owner {
		t.Fatal("new work should be pending")
	}
	if !s.Activate("key", done) {
		t.Fatal("active work should start")
	}
	active, wait := s.Cancel("key")
	if !active || wait != done {
		t.Fatal("active cancellation did not return completion channel")
	}
	s.Finish("key", "hash", []byte("response"), done, true)
	_, result, _ = s.Begin("key", "hash")
	if result != Cancelled {
		t.Fatal("cancelled work must not replay")
	}

	_, result, completed := s.Begin("complete", "hash")
	if result != Owner {
		t.Fatal("completed work should start once")
	}
	s.Finish("complete", "hash", []byte("response"), completed, true)
	e, result, _ := s.Begin("complete", "hash")
	if result != Replay || string(e.Response) != "response" {
		t.Fatal("completed work should replay")
	}
}

func TestPendingRequestHashBindingAndSharedCompletion(t *testing.T) {
	s := New()
	_, result, done := s.Begin("key", "first")
	if result != Owner {
		t.Fatal("first work should own the key")
	}
	type reply struct {
		result BeginResult
		done   chan struct{}
	}
	sameHash := make(chan reply, 1)
	differentHash := make(chan reply, 1)
	go func() {
		_, result, done := s.Begin("key", "first")
		sameHash <- reply{result: result, done: done}
	}()
	go func() {
		_, result, done := s.Begin("key", "second")
		differentHash <- reply{result: result, done: done}
	}()
	same := <-sameHash
	if same.result != Pending || same.done != done {
		t.Fatal("same hash must join the existing completion")
	}
	different := <-differentHash
	if different.result != Mismatch || different.done != nil {
		t.Fatal("different hash must be rejected while work is pending")
	}
	s.Finish("key", "first", []byte("response"), done, true)
	select {
	case <-same.done:
	default:
		t.Fatal("joined request was not completed")
	}
	entry, result := s.Resolve("key", "first")
	if result != Replay || string(entry.Response) != "response" {
		t.Fatal("joined request did not resolve to the completed response")
	}
}

func TestPendingDuplicateRetainsCancellationCompletion(t *testing.T) {
	s := New()
	_, result, done := s.Begin("key", "hash")
	if result != Owner {
		t.Fatal("first work should own the key")
	}
	_, result, joined := s.Begin("key", "hash")
	if result != Pending || joined != done {
		t.Fatal("duplicate should share the original completion")
	}
	active, cancelledDone := s.Cancel("key")
	if active || cancelledDone != done {
		t.Fatal("cancellation did not retain the original completion")
	}
	s.Finish("key", "hash", nil, done, false)
	select {
	case <-joined:
	default:
		t.Fatal("duplicate waiter was not released by cancellation")
	}
}

func TestQueuedUnknownAndRepeatedCancellationAreIdempotent(t *testing.T) {
	s := New()
	_, result, queuedDone := s.Begin("queued", "hash")
	if result != Owner {
		t.Fatal("queued work should start once")
	}
	active, done := s.Cancel("queued")
	if active || done != queuedDone {
		t.Fatal("queued cancellation did not expose its completion")
	}
	active, repeated := s.Cancel("queued")
	if active || repeated != queuedDone {
		t.Fatal("repeated queued cancellation changed state")
	}
	s.Finish("queued", "hash", nil, queuedDone, false)
	select {
	case <-queuedDone:
	default:
		t.Fatal("queued cancellation did not complete")
	}
	if active, done = s.Cancel("unknown"); active || done != nil {
		t.Fatal("unknown cancellation must complete immediately")
	}
	if active, done = s.Cancel("unknown"); active || done != nil {
		t.Fatal("repeated unknown cancellation must complete immediately")
	}
}
