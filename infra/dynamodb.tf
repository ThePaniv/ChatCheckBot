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
  range_key    = "check_id"

  attribute {
    name = "user_id"
    type = "S"
  }

  # One row per prompt; the sort key is the check's send-time id (epoch seconds).
  attribute {
    name = "check_id"
    type = "S"
  }
}
