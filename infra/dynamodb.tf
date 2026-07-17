resource "aws_dynamodb_table" "users" {
  name         = "WaterBotUsers"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"

  attribute {
    name = "user_id"
    type = "S"
  }
}

resource "aws_dynamodb_table" "logs" {
  name         = "WaterBotLogs"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "user_id"
  range_key    = "checked_at"

  attribute {
    name = "user_id"
    type = "S"
  }

  # One row per prompt; the sort key is when the prompt was sent (epoch seconds),
  # which is also the token echoed back in the answer callback.
  attribute {
    name = "checked_at"
    type = "S"
  }
}
