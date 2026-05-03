// L1 PATCH: Add GET /api/v1/block/{height} endpoint
// =====================================================================
// File: src/testnet_node.cpp
// Insert location: After the existing `/api/v1/blocks` handler (around
//                  line 6515, before the `/api/v1/balances` handler).
//
// Purpose: The off-chain staking service needs to read full block contents
//          (including all transactions) to build its stake ledger by
//          watching TRANSFERs to/from the escrow address. The existing
//          /api/v1/blocks endpoint only returns the last 10 blocks as
//          metadata-only (no tx data), which is insufficient.
//
// Risk: ZERO consensus impact. This is a read-only RPC handler. It does
//       not touch block validation, mempool, or staking state.
//
// Deploy: Same procedure as any other binary update — pull, rebuild with
//         -O0 (Seed 1's 20GB constraint), swap binary, restart.
// =====================================================================

    else if (path.substr(0, 16) == "/api/v1/block/" && path.size() > 16) {
        // Parse height from path: /api/v1/block/12345
        std::string height_str = path.substr(14);  // strip "/api/v1/block/"
        uint64_t target_height;
        try {
            target_height = std::stoull(height_str);
        } catch (...) {
            response = make_json_response("{\"error\":\"invalid_height\"}", 400);
            goto done_request;  // or your existing response-finalization label
        }

        std::lock_guard<std::mutex> lock(g_chain_mutex);
        if (target_height >= g_chain->blocks.size()) {
            response = make_json_response("{\"error\":\"height_out_of_range\"}", 404);
            goto done_request;
        }

        const auto& b = g_chain->blocks[target_height];
        std::ostringstream ss;
        ss << std::fixed << std::setprecision(0);
        ss << "{";
        ss << "\"height\":" << b.height;
        ss << ",\"hash\":\"" << to_hex(b.hash) << "\"";
        ss << ",\"prev_hash\":\"" << to_hex(b.prev_hash) << "\"";
        ss << ",\"miner\":\"" << json_escape(b.miner_address) << "\"";
        ss << ",\"reward\":" << b.reward;
        ss << ",\"reward_npc\":" << (b.reward / 100000000.0);
        ss << ",\"difficulty\":" << b.difficulty;
        ss << ",\"timestamp\":" << b.timestamp;
        ss << ",\"total_fees\":" << b.total_fees;
        ss << ",\"fees_burned\":" << b.fees_burned;
        ss << ",\"fees_to_miner\":" << b.fees_to_miner;
        ss << ",\"fees_to_stakers\":" << b.fees_to_stakers;
        ss << ",\"transactions\":[";

        bool first_tx = true;
        for (const auto& tx : b.transactions) {
            if (!first_tx) ss << ",";
            first_tx = false;

            std::string type_str;
            switch (tx.type) {
                case TxType::TRANSFER: type_str = "transfer"; break;
                case TxType::STAKE: type_str = "stake"; break;
                case TxType::UNSTAKE: type_str = "unstake"; break;
                case TxType::DELEGATE: type_str = "delegate"; break;
                case TxType::UNDELEGATE: type_str = "undelegate"; break;
                case TxType::DATA: type_str = "data"; break;
                case TxType::MARKETPLACE: type_str = "marketplace"; break;
                case TxType::ADMIN_MIGRATE: type_str = "admin_migrate"; break;
                default: type_str = "unknown"; break;
            }

            ss << "{";
            ss << "\"type\":\"" << type_str << "\"";
            ss << ",\"from\":\"" << json_escape(tx.from) << "\"";
            ss << ",\"to\":\"" << json_escape(tx.to) << "\"";
            ss << ",\"amount\":" << tx.amount;
            ss << ",\"fee\":" << tx.fee;
            ss << ",\"tx_hash\":\"" << to_hex(tx.compute_hash()) << "\"";
            ss << ",\"id\":" << tx.id;
            ss << ",\"confirmed_height\":" << tx.confirmed_height;
            // Optional fields — include only if set
            if (!tx.memo.empty()) {
                ss << ",\"memo\":\"" << json_escape(tx.memo) << "\"";
            }
            ss << "}";
        }

        ss << "]}";
        response = make_json_response(ss.str());
    }


// =====================================================================
// ALSO ADD: Light-weight bulk endpoint for snapshot efficiency
// =====================================================================
// Walking 46K blocks one-at-a-time over HTTP is slow (~46K requests).
// Add a /api/v1/blocks/range?from=N&to=M endpoint that returns
// transactions only (skipping per-block metadata for speed) for a
// height range. This is a snapshot-time-only endpoint; live watcher
// uses /api/v1/block/{height} for individual blocks as they're produced.
//
// Insert immediately after the per-block handler above.
// =====================================================================

    else if (path.substr(0, 22) == "/api/v1/blocks/range") {
        // Parse query string: ?from=N&to=M
        size_t qpos = path.find('?');
        if (qpos == std::string::npos) {
            response = make_json_response("{\"error\":\"missing_query\"}", 400);
            goto done_request;
        }

        std::string qs = path.substr(qpos + 1);
        uint64_t from_h = 0, to_h = 0;
        // Quick-and-dirty parse — production code should use a real parser
        size_t fpos = qs.find("from=");
        size_t tpos = qs.find("to=");
        if (fpos == std::string::npos || tpos == std::string::npos) {
            response = make_json_response("{\"error\":\"missing_from_or_to\"}", 400);
            goto done_request;
        }
        try {
            from_h = std::stoull(qs.substr(fpos + 5));
            to_h = std::stoull(qs.substr(tpos + 3));
        } catch (...) {
            response = make_json_response("{\"error\":\"invalid_range\"}", 400);
            goto done_request;
        }

        // Cap at 100 blocks per request to bound memory + response size
        const uint64_t MAX_RANGE = 100;
        if (to_h < from_h || to_h - from_h >= MAX_RANGE) {
            response = make_json_response("{\"error\":\"range_too_large_or_invalid\"}", 400);
            goto done_request;
        }

        std::lock_guard<std::mutex> lock(g_chain_mutex);
        if (to_h >= g_chain->blocks.size()) {
            to_h = g_chain->blocks.size() - 1;
        }

        std::ostringstream ss;
        ss << std::fixed << std::setprecision(0);
        ss << "{\"from\":" << from_h << ",\"to\":" << to_h << ",\"blocks\":[";
        bool first_block = true;
        for (uint64_t h = from_h; h <= to_h; ++h) {
            if (!first_block) ss << ",";
            first_block = false;
            const auto& b = g_chain->blocks[h];
            ss << "{\"height\":" << b.height;
            ss << ",\"miner\":\"" << json_escape(b.miner_address) << "\"";
            ss << ",\"reward\":" << b.reward;
            ss << ",\"fees_to_stakers\":" << b.fees_to_stakers;
            ss << ",\"transactions\":[";

            bool first_tx = true;
            for (const auto& tx : b.transactions) {
                if (!first_tx) ss << ",";
                first_tx = false;
                std::string type_str;
                switch (tx.type) {
                    case TxType::TRANSFER: type_str = "transfer"; break;
                    case TxType::STAKE: type_str = "stake"; break;
                    case TxType::UNSTAKE: type_str = "unstake"; break;
                    case TxType::DELEGATE: type_str = "delegate"; break;
                    case TxType::UNDELEGATE: type_str = "undelegate"; break;
                    default: type_str = "other"; break;
                }
                ss << "{\"type\":\"" << type_str << "\"";
                ss << ",\"from\":\"" << json_escape(tx.from) << "\"";
                ss << ",\"to\":\"" << json_escape(tx.to) << "\"";
                ss << ",\"amount\":" << tx.amount;
                ss << ",\"tx_hash\":\"" << to_hex(tx.compute_hash()) << "\"";
                ss << "}";
            }
            ss << "]}";
        }
        ss << "]}";
        response = make_json_response(ss.str());
    }

// =====================================================================
// END OF L1 PATCH
// =====================================================================
//
// NOTE on `goto done_request`:
// The existing RPC handler structure may use early-return or fall-through
// rather than a `done_request` label. Adapt the error returns to match
// the existing pattern in the file. The functional logic above is what
// matters; the control flow glue is mechanical.
//
// DEPLOY CHECKLIST:
//   1. Apply this patch to src/testnet_node.cpp on local Windows repo
//   2. git add + commit + push
//   3. SSH seed 1 → git pull → rebuild (mine.sh build flow, -O0)
//   4. mv old binary aside, swap new in
//   5. SIGTERM running node, restart
//   6. Verify with: curl http://localhost:18333/api/v1/block/100
//      Should return JSON with full block contents including transactions
//   7. Repeat on seed 2
//
// This patch must land BEFORE the staking service can run its initial
// snapshot, since the snapshot depends on the per-block endpoint.
