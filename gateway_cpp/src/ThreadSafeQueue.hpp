#pragma once
// ===========================================================================
//  ThreadSafeQueue — Bounded Producer-Consumer Queue
// ===========================================================================
//  Thread-safe bounded queue using std::mutex + std::condition_variable.
//  
//  Overflow policy: drop-oldest (configurable).
//  When the queue is full, the oldest item is discarded so the most recent
//  telemetry is always available — stale data is less useful than fresh data.
//
//  Used to decouple:
//    Poller thread (producer) → Queue → Dispatcher thread (consumer)
//
//  Why bounded?
//    If the MQTT broker goes down, an unbounded queue would consume all
//    available memory. A bounded queue with drop-oldest ensures the system
//    remains stable under backpressure.
// ===========================================================================

#ifndef THREAD_SAFE_QUEUE_HPP
#define THREAD_SAFE_QUEUE_HPP

#include <atomic>
#include <chrono>
#include <condition_variable>
#include <deque>
#include <mutex>
#include <optional>
#include <vector>

template <typename T>
class ThreadSafeQueue {
public:
    explicit ThreadSafeQueue(size_t max_size = 1000)
        : max_size_(max_size)
        , enqueued_(0)
        , dequeued_(0)
        , dropped_(0) {}

    // Non-copyable, non-movable
    ThreadSafeQueue(const ThreadSafeQueue&) = delete;
    ThreadSafeQueue& operator=(const ThreadSafeQueue&) = delete;

    /// Push an item. If queue is full, drop the oldest item.
    void push(T item) {
        std::lock_guard<std::mutex> lock(mutex_);
        if (queue_.size() >= max_size_) {
            queue_.pop_front();  // drop oldest
            dropped_.fetch_add(1, std::memory_order_relaxed);
        }
        queue_.push_back(std::move(item));
        enqueued_.fetch_add(1, std::memory_order_relaxed);
        cv_.notify_one();
    }

    /// Pop an item, blocking until one is available or timeout expires.
    /// Returns std::nullopt on timeout.
    std::optional<T> pop(std::chrono::milliseconds timeout = std::chrono::milliseconds(1000)) {
        std::unique_lock<std::mutex> lock(mutex_);
        if (!cv_.wait_for(lock, timeout, [this] { return !queue_.empty(); })) {
            return std::nullopt;  // timeout
        }
        T item = std::move(queue_.front());
        queue_.pop_front();
        dequeued_.fetch_add(1, std::memory_order_relaxed);
        return item;
    }

    /// Try to pop without blocking. Returns std::nullopt if empty.
    std::optional<T> try_pop() {
        std::lock_guard<std::mutex> lock(mutex_);
        if (queue_.empty()) return std::nullopt;
        T item = std::move(queue_.front());
        queue_.pop_front();
        dequeued_.fetch_add(1, std::memory_order_relaxed);
        return item;
    }

    /// Drain all remaining items (used during shutdown).
    std::vector<T> drain() {
        std::lock_guard<std::mutex> lock(mutex_);
        std::vector<T> items;
        items.reserve(queue_.size());
        while (!queue_.empty()) {
            items.push_back(std::move(queue_.front()));
            queue_.pop_front();
            dequeued_.fetch_add(1, std::memory_order_relaxed);
        }
        return items;
    }

    /// Wake all waiting consumers (used during shutdown).
    void notify_all() {
        cv_.notify_all();
    }

    // --- Accessors ---
    size_t size() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return queue_.size();
    }

    bool empty() const {
        std::lock_guard<std::mutex> lock(mutex_);
        return queue_.empty();
    }

    uint64_t enqueued_count() const { return enqueued_.load(std::memory_order_relaxed); }
    uint64_t dequeued_count() const { return dequeued_.load(std::memory_order_relaxed); }
    uint64_t dropped_count()  const { return dropped_.load(std::memory_order_relaxed);  }

private:
    mutable std::mutex      mutex_;
    std::condition_variable cv_;
    std::deque<T>           queue_;
    size_t                  max_size_;

    std::atomic<uint64_t>   enqueued_;
    std::atomic<uint64_t>   dequeued_;
    std::atomic<uint64_t>   dropped_;
};

#endif // THREAD_SAFE_QUEUE_HPP
