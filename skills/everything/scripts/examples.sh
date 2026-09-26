#!/usr/bin/env bash
# Sample invocations of the everything CLI.
set -euo pipefail

# Echoes back the input string
cli-use everything echo --message <message>

# Demonstrates how annotations can be used to provide metadata about content.
cli-use everything get-annotated-message --messageType <messageType>

# Returns all environment variables, helpful for debugging MCP server configuration
cli-use everything get-env

# Returns up to ten resource links that reference different types of resources
cli-use everything get-resource-links

# Returns a resource reference that can be used by MCP clients
cli-use everything get-resource-reference

# Returns structured content along with an output schema for client data validation
cli-use everything get-structured-content --location <location>

# Returns the sum of two numbers
cli-use everything get-sum --a <a> --b <b>

# Returns a tiny MCP logo image.
cli-use everything get-tiny-image

# Compresses a single file using gzip compression. Depending upon the selected output type, returns either the compressed data as a gzipped resource or a resource link, allowing it to be downloaded in a subsequent request during the current session.
cli-use everything gzip-file-as-resource

# Toggles simulated, random-leveled logging on or off.
cli-use everything toggle-simulated-logging
