import Foundation
import AsyncHTTPClient
@preconcurrency import SwiftEccodes
import NIOCore

/**
 CDS APIv2 flow:
 
 Submit request
 
 curl --request POST \
   --url https://cds-beta.climate.copernicus.eu/api/retrieve/v1/processes/reanalysis-era5-single-levels/execute \
   --header 'PRIVATE-TOKEN: 169d504d-3axxxxxxxxxxxxxxxx" \
   --data '{"inputs":
 {
     "product_type": ["reanalysis"],
     "variable": ["2m_temperature"],
     "year": ["2024"],
     "month": ["08"],
     "day": ["01"],
     "time": ["00:00"],
     "data_format": "grib",
     "download_format": "unarchived"
 }
 }'
 RETURNS:
 {
   "processID": "reanalysis-era5-single-levels",
   "type": "process",
   "jobID": "b4498619-24a9-41d4-9c7f-2a4fb00a4704",
   "status": "accepted",
   "created": "2024-08-29T09:51:47.098588",
   "updated": "2024-08-29T09:51:47.098588",
   "links": [
     {
       "href": "https://cds-beta.climate.copernicus.eu/api/retrieve/v1/processes/reanalysis-era5-single-levels/execute",
       "rel": "self"
     },
     {
       "href": "https://cds-beta.climate.copernicus.eu/api/retrieve/v1/jobs/b4498619-24a9-41d4-9c7f-2a4fb00a4704",
       "rel": "monitor",
       "type": "application/json",
       "title": "job status info"
     }
   ],
   "metadata": {
     "datasetMetadata": {
       "messages": []
     }
   }
 }
 
 Check status at: https://cds-beta.climate.copernicus.eu/api/retrieve/v1/jobs/b4498619-24a9-41d4-9c7f-2a4fb00a4704
 RETURNS
 {
   "processID": "reanalysis-era5-single-levels",
   "type": "process",
   "jobID": "b4498619-24a9-41d4-9c7f-2a4fb00a4704",
   "status": "accepted",
   "created": "2024-08-29T09:51:47.098588",
   "updated": "2024-08-29T09:51:47.098588",
   "links": [
     {
       "href": "https://cds-beta.climate.copernicus.eu/api/retrieve/v1/jobs/b4498619-24a9-41d4-9c7f-2a4fb00a4704",
       "rel": "self",
       "type": "application/json"
     }
   ]
 }
 
 Once finished:
 {
   "processID": "reanalysis-era5-single-levels",
   "type": "process",
   "jobID": "22e43b24-f036-41ba-a6d8-c7567926b69f",
   "status": "successful",
   "created": "2024-08-29T09:46:44.706285",
   "started": "2024-08-29T09:46:47.428596",
   "finished": "2024-08-29T09:46:52.303671",
   "updated": "2024-08-29T09:46:52.303671",
   "links": [
     {
       "href": "https://cds-beta.climate.copernicus.eu/api/retrieve/v1/jobs/22e43b24-f036-41ba-a6d8-c7567926b69f",
       "rel": "self",
       "type": "application/json"
     },
     {
       "href": "https://cds-beta.climate.copernicus.eu/api/retrieve/v1/jobs/22e43b24-f036-41ba-a6d8-c7567926b69f/results",
       "rel": "results"
     }
   ]
 }
 
 Result can be fetched here:
 https://cds-beta.climate.copernicus.eu/api/retrieve/v1/jobs/22e43b24-f036-41ba-a6d8-c7567926b69f/results
 RETURNS:
 {
   "asset": {
     "value": {
       "type": "application/x-grib",
       "href": "https://object-store.os-api.cci2.ecmwf.int:443/cci2-prod-cache/1ba8b427bbe3033b92f33aef06adc471.grib",
       "file:checksum": "f41af2fd14a83e191ef3097c5f3c1a0c",
       "file:size": 2076588,
       "file:local_path": "s3://cci2-prod-cache/1ba8b427bbe3033b92f33aef06adc471.grib"
     }
   }
 }
 */

enum CdsApiError: Error {
    case jobAborted
    case startError(code: Int, message: String)
    case submissionRejected(code: Int, message: String)
    case error(message: String, reason: String)
    case waiting(status: CdsState)
    case uncertainSubmission(stateFile: String)
    case invalidResponse(message: String)
    case restrictedAccessToValidData
    case invalidCombinationOfValues
}

enum CdsState: String, Codable {
    case accepted
    case failed
    case successful
    case running
}

fileprivate struct CdsApiResponse: Codable {
    /// E.g. `reanalysis-era5-single-levels`
    let processID: String
    let status: CdsState
    let jobID: String
}

fileprivate enum CdsApiResumePhase: String, Codable {
    case submitting
    case submitted
}

/// Persisted only when WEATHER_CDS_JOB_STATE_FILE is explicitly configured.
/// It contains no API key. Keeping the original POST body makes it impossible
/// to resume a job for a different dataset, date, variable list, or crop.
fileprivate struct CdsApiResumeState: Codable {
    let version: Int
    let dataset: String
    let server: String
    let requestBody: Data
    /// Optional for backward compatibility with version 1 state files.  A
    /// version 2 `submitting` intent deliberately has no job ID yet.
    let phase: CdsApiResumePhase?
    let job: CdsApiResponse?
}

fileprivate struct CdsApiResults: Decodable {
    let asset: Asset

    struct Asset: Decodable {
        let value: Value
    }
    struct Value: Decodable {
        /// application/x-grib
        let type: String
        let href: String
        let checksum: String
        let size: Int
        let local_path: String

        enum CodingKeys: String, CodingKey {
            case type
            case href
            case checksum = "file:checksum"
            case size = "file:size"
            case local_path = "file:local_path"
        }
    }
}

fileprivate struct CdsApiResultsError: Decodable {
    let type: String
    let title: String
    let status: Int
    let traceback: String
}

extension Curl {
    /**
     Get GRIB data from the CDS API
     */
    func withCdsApi<Query: Encodable, T>(dataset: String, query: Query, apikey: String, server: String = "https://cds.climate.copernicus.eu/api", body: (AnyAsyncSequence<GribMessage>) async throws -> (T)) async throws -> T {
        let requestBody = try encodeCdsApiRequest(query: query)
        let job = try await loadOrStartCdsApiJob(dataset: dataset, requestBody: requestBody, apikey: apikey, server: server)
        let results = try await waitForCdsJobPreservingResumeState(job: job, apikey: apikey, server: server)
        let result = try await withGribStream(url: results.asset.value.href, bzip2Decode: false, body: body)
        try await cleanupCdsApiJob(job: job, apikey: apikey, server: server)
        try removeCdsApiResumeState()
        return result
    }

    /**
     Get GRIB data from the CDS API and store to file
     */
    func downloadCdsApi<Query: Encodable>(dataset: String, query: Query, apikey: String, server: String = "https://cds.climate.copernicus.eu/api", destinationFile: String) async throws {
        let requestBody = try encodeCdsApiRequest(query: query)
        let job = try await loadOrStartCdsApiJob(dataset: dataset, requestBody: requestBody, apikey: apikey, server: server)
        let results = try await waitForCdsJobPreservingResumeState(job: job, apikey: apikey, server: server)
        try await download(url: results.asset.value.href, toFile: destinationFile, bzip2Decode: false, minSize: results.asset.value.size)
        try await cleanupCdsApiJob(job: job, apikey: apikey, server: server)
        try removeCdsApiResumeState()
    }

    fileprivate var cdsApiStateFile: String? {
        guard let value = ProcessInfo.processInfo.environment["WEATHER_CDS_JOB_STATE_FILE"]?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else {
            return nil
        }
        return value
    }

    fileprivate var cdsApiPollIntervalSeconds: Int {
        guard let value = ProcessInfo.processInfo.environment["WEATHER_CDS_POLL_INTERVAL_SECONDS"], let parsed = Int(value), (1...3600).contains(parsed) else {
            return 1
        }
        return parsed
    }

    fileprivate var cdsApiJobDeadline: Date {
        guard let value = ProcessInfo.processInfo.environment["WEATHER_CDS_JOB_TIMEOUT_HOURS"], let parsed = Double(value), parsed >= 1, parsed <= 24 * 30 else {
            return deadline
        }
        return Date().addingTimeInterval(parsed * 3600)
    }

    fileprivate func encodeCdsApiRequest<Query: Encodable>(query: Query) throws -> Data {
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(["inputs": query])
    }

    fileprivate func loadOrStartCdsApiJob(dataset: String, requestBody: Data, apikey: String, server: String) async throws -> CdsApiResponse {
        if let path = cdsApiStateFile, FileManager.default.fileExists(atPath: path) {
            let attributes = try FileManager.default.attributesOfItem(atPath: path)
            guard attributes[.type] as? FileAttributeType == .typeRegular else {
                throw CdsApiError.error(message: "Refusing non-regular CDS resume state", reason: path)
            }
            let state = try JSONDecoder().decode(CdsApiResumeState.self, from: Data(contentsOf: URL(fileURLWithPath: path)))
            guard (state.version == 1 || state.version == 2), state.dataset == dataset, state.server == server, state.requestBody == requestBody else {
                throw CdsApiError.error(message: "CDS resume state does not match this request", reason: path)
            }
            if let job = state.job {
                logger.info("Resuming existing CDS job \(job.jobID) from \(path)")
                return job
            }
            // A process may have died after the server accepted POST but
            // before its response/job ID was persisted.  CDS exposes neither
            // an idempotency key nor a safe request lookup, so fail closed:
            // never automatically POST the same request again.
            throw CdsApiError.uncertainSubmission(stateFile: path)
        }
        try saveCdsApiResumeState(
            dataset: dataset,
            server: server,
            requestBody: requestBody,
            phase: .submitting,
            job: nil
        )
        do {
            // POST is intentionally one-shot.  Any network ambiguity leaves
            // the durable submitting intent in place instead of risking a
            // duplicate remote queue entry.
            let job = try await startCdsApiJob(dataset: dataset, requestBody: requestBody, apikey: apikey, server: server)
            try saveCdsApiResumeState(
                dataset: dataset,
                server: server,
                requestBody: requestBody,
                phase: .submitted,
                job: job
            )
            return job
        } catch let error as CdsApiError {
            switch error {
            case .invalidCombinationOfValues, .submissionRejected:
                // ADS explicitly rejected the POST, so no remote job exists.
                try removeCdsApiResumeState()
            case .jobAborted, .startError, .error, .waiting, .uncertainSubmission, .invalidResponse, .restrictedAccessToValidData:
                break
            }
            throw error
        }
    }

    fileprivate func saveCdsApiResumeState(dataset: String, server: String, requestBody: Data, phase: CdsApiResumePhase, job: CdsApiResponse?) throws {
        guard let path = cdsApiStateFile else {
            return
        }
        let url = URL(fileURLWithPath: path)
        let parent = url.deletingLastPathComponent()
        try FileManager.default.createDirectory(at: parent, withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: path) {
            let attributes = try FileManager.default.attributesOfItem(atPath: path)
            guard attributes[.type] as? FileAttributeType == .typeRegular else {
                throw CdsApiError.error(message: "Refusing non-regular CDS resume state", reason: path)
            }
        }
        let state = CdsApiResumeState(version: 2, dataset: dataset, server: server, requestBody: requestBody, phase: phase, job: job)
        let encoder = JSONEncoder()
        encoder.outputFormatting = [.sortedKeys]
        try encoder.encode(state).write(to: url, options: .atomic)
        if let job {
            logger.info("Persisted CDS job \(job.jobID) to \(path)")
        } else {
            logger.info("Persisted CDS submitting intent to \(path)")
        }
    }

    fileprivate func removeCdsApiResumeState() throws {
        guard let path = cdsApiStateFile, FileManager.default.fileExists(atPath: path) else {
            return
        }
        let attributes = try FileManager.default.attributesOfItem(atPath: path)
        guard attributes[.type] as? FileAttributeType == .typeRegular else {
            throw CdsApiError.error(message: "Refusing non-regular CDS resume state", reason: path)
        }
        try FileManager.default.removeItem(atPath: path)
    }

    /// A timeout, cancellation, network outage, or transient 404 preserves the
    /// persisted job ID so the next process resumes the same ADS queue entry.
    /// Only an explicit terminal job failure clears it and permits a later POST.
    fileprivate func waitForCdsJobPreservingResumeState(job: CdsApiResponse, apikey: String, server: String) async throws -> CdsApiResults {
        do {
            return try await waitForCdsJob(job: job, apikey: apikey, server: server)
        } catch let error as CdsApiError {
            switch error {
            case .jobAborted, .error, .restrictedAccessToValidData:
                try? await cleanupCdsApiJob(job: job, apikey: apikey, server: server)
                try removeCdsApiResumeState()
            case .startError, .submissionRejected, .waiting, .uncertainSubmission, .invalidResponse, .invalidCombinationOfValues:
                break
            }
            throw error
        }
    }

    /// Start a new job using POST
    fileprivate func startCdsApiJob(dataset: String, requestBody: Data, apikey: String, server: String) async throws -> CdsApiResponse {
        // var request = HTTPClientRequest(url: "\(server)/resources/\(dataset)")
        var request = HTTPClientRequest(url: "\(server)/retrieve/v1/processes/\(dataset)/execute")

        request.method = .POST
        request.headers.add(name: "PRIVATE-TOKEN", value: apikey)
        request.headers.add(name: "content-type", value: "application/json")
        request.body = .bytes(ByteBuffer(data: requestBody))

        let response = try await client.execute(request, timeout: .seconds(60), logger: logger)
        guard (200..<300).contains(response.status.code) else {
            let message = try await response.readStringImmutable() ?? ""
            if response.status.code == 400, message.contains("Request has not produced a valid combination of values") {
                throw CdsApiError.invalidCombinationOfValues
            }
            if (400..<500).contains(response.status.code) {
                throw CdsApiError.submissionRejected(code: response.status.code, message: message)
            }
            throw CdsApiError.startError(code: response.status.code, message: message)
        }
        guard let job = try await response.readJSONDecodable(CdsApiResponse.self) else {
            throw CdsApiError.invalidResponse(message: "Could not decode CDS job response")
        }
        logger.info("Submitted job \(job)")
        return job
    }

    /// Wait for josb to finish and return download URL
    fileprivate func waitForCdsJob(job: CdsApiResponse, apikey: String, server: String) async throws -> CdsApiResults {
        let timeout = TimeoutTracker(logger: self.logger, deadline: cdsApiJobDeadline)
        var job = job
        let backoff = ExponentialBackOff(maximum: .seconds(30))
        while true {
            switch job.status {
            case .accepted, .running:
                try await timeout.check(error: CdsApiError.waiting(status: job.status), delay: cdsApiPollIntervalSeconds)

                var request = HTTPClientRequest(url: "\(server)/retrieve/v1/jobs/\(job.jobID)")
                request.headers.add(name: "PRIVATE-TOKEN", value: apikey)
                /// CDS may return error 404 from time to time......
                let response = try await client.executeRetry(
                    request,
                    logger: logger,
                    deadline: cdsApiJobDeadline,
                    backOffSettings: backoff,
                    error404WaitTime: .seconds(Int64(cdsApiPollIntervalSeconds))
                )
                guard (200..<300).contains(response.status.code), let jobNext = try await response.readJSONDecodable(CdsApiResponse.self) else {
                    throw CdsApiError.invalidResponse(message: "Could not decode CDS job status")
                }
                job = jobNext
            case .failed:
                var request = HTTPClientRequest(url: "\(server)/retrieve/v1/jobs/\(job.jobID)/results")
                request.headers.add(name: "PRIVATE-TOKEN", value: apikey)
                let response = try await client.executeRetry(
                    request,
                    logger: logger,
                    deadline: cdsApiJobDeadline,
                    backOffSettings: backoff,
                    error404WaitTime: .seconds(Int64(cdsApiPollIntervalSeconds))
                )
                guard (200..<300).contains(response.status.code), let results = try await response.readJSONDecodable(CdsApiResultsError.self) else {
                    throw CdsApiError.invalidResponse(message: "Could not decode failed CDS job results")
                }
                if results.traceback.contains("The job failed with: ValueError") {
                    throw CdsApiError.restrictedAccessToValidData
                }
                throw CdsApiError.error(message: results.title, reason: results.traceback)
            case .successful:
                var request = HTTPClientRequest(url: "\(server)/retrieve/v1/jobs/\(job.jobID)/results")
                request.headers.add(name: "PRIVATE-TOKEN", value: apikey)
                let response = try await client.executeRetry(
                    request,
                    logger: logger,
                    deadline: cdsApiJobDeadline,
                    backOffSettings: backoff,
                    error404WaitTime: .seconds(Int64(cdsApiPollIntervalSeconds))
                )
                guard (200..<300).contains(response.status.code), let results = try await response.readJSONDecodable(CdsApiResults.self) else {
                    throw CdsApiError.invalidResponse(message: "Could not decode successful CDS job results")
                }
                return results
            }
        }
    }

    fileprivate func cleanupCdsApiJob(job: CdsApiResponse, apikey: String, server: String) async throws {
        var request = HTTPClientRequest(url: "\(server)/retrieve/v1/jobs/\(job.jobID)")
        request.method = .DELETE
        request.headers.add(name: "PRIVATE-TOKEN", value: apikey)
        _ = try await client.executeRetry(request, logger: logger)
    }
}
