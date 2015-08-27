package main

import (
	"flag"
	"fmt"
	"log"
	"strings"
	"time"

	"github.com/dooferlad/here"
	"github.com/nickvanw/ircx"
	"github.com/sorcix/irc"

	"encoding/json"
	"net/http"

	"github.com/googollee/go-socket.io"
	"github.com/gorilla/mux"
	"gopkg.in/mgo.v2"
	"gopkg.in/mgo.v2/bson"
)

var (
	name   = flag.String("name", "dooferbot", "Nick to use in IRC")
	server = flag.String("server", "chat.freenode.org:6667", "Host:Port to connect to")
)

// Non-RFC 1459 messages are defined below. Source: https://www.alien.net.au/irc/irc2numerics.html
const (
	RPL_LOCALUSERS   = "265"
	RPL_GLOBALUSERS  = "266"
	RPL_STATSDLINE   = "250"
	RPL_TOPICWHOTIME = "333"
)

func init() {
	flag.Parse()
}

type ServerState struct {
	chatMessages *mgo.Collection
	chans        *mgo.Collection
	server       *mgo.Collection
	messages     chan string
	socket       *socketio.Socket
	bot          *ircx.Bot

	namesState NamesState
}

// collectionToJson sends all data in a Mongo collection as JSON to a client
// by writing it directly into the HTTP response.
func collectionToJson(response http.ResponseWriter, collection *mgo.Collection) {
	var data []bson.M
	query := collection.Find(nil)
	count, err := query.Count()
	if err != nil {
		http.Error(response, err.Error(), http.StatusInternalServerError)
		return
	}
	response.Header().Set("Content-Type", "application/vnd.api+json")
	if count > 0 {
		// We don't want to encode a zero length list as "null", so we only
		// write a response if there is something to write. This makes JSON
		// decoders happier.
		err = query.All(&data)
		if err != nil {
			http.Error(response, err.Error(), http.StatusInternalServerError)
			return
		}
		for i := range data {
			delete(data[i], "_id")
		}
		json.NewEncoder(response).Encode(&data)
	}
}

func (state *ServerState) apiChanHandler(response http.ResponseWriter, request *http.Request) {
	collectionToJson(response, state.chans)
}

func (state *ServerState) apiServerHandler(response http.ResponseWriter, request *http.Request) {
	collectionToJson(response, state.server)
}

func (state *ServerState) apiPrivChatMessageHandler(response http.ResponseWriter, request *http.Request) {
	vars := mux.Vars(request)
	if channel, ok := vars["channel"]; ok {
		state.apiChatMessageHandler(response, request, channel)
	}
}

func (state *ServerState) apiChanChatMessageHandler(response http.ResponseWriter, request *http.Request) {
	vars := mux.Vars(request)
	if channel, ok := vars["channel"]; ok {
		state.apiChatMessageHandler(response, request, "#"+channel)
	}
}

type MessageToSend struct {
	Channel string `json:"c"`
	Message string `json:"m"`
}

func (state *ServerState) apiSendMessageHandler(response http.ResponseWriter, request *http.Request) {
	decoder := json.NewDecoder(request.Body)

	var data MessageToSend
	err := decoder.Decode(&data)
	if err != nil {
		http.Error(response, err.Error(), http.StatusInternalServerError)
		return
	}
	fmt.Printf("%+v\n", data)
	state.SendMessage(data.Channel, data.Message)
}

func (state *ServerState) apiChatMessageHandler(response http.ResponseWriter, request *http.Request, channel string) {

	var data []ChatMessage
	q := bson.M{"channel": channel}
	query := state.chatMessages.Find(q).Sort("Timestamp")
	count, err := query.Count()
	if err != nil {
		http.Error(response, err.Error(), http.StatusInternalServerError)
		return
	}
	response.Header().Set("Content-Type", "application/vnd.api+json")
	if count > 0 {
		// We don't want to encode a zero length list as "null", so we only
		// write a response if there is something to write. This makes JSON
		// decoders happier.
		err = query.All(&data)
		if err != nil {
			http.Error(response, err.Error(), http.StatusInternalServerError)
			return
		}
		ts := data[0].Timestamp
		for i := 1; i < len(data); i++ {
			data[i].Timestamp -= ts
		}
		json.NewEncoder(response).Encode(&data)
	}

}

func (state *ServerState) ping() {
	state.messages <- "ping"
}
func (state *ServerState) apiPing(response http.ResponseWriter, request *http.Request) {
	state.ping()
}

// forwardUpdatesToSocketIO sends a ping to the connected clients when we
// receive a ping from a data source that something updated.
func (state *ServerState) forwardUpdatesToSocketIO() {
	server, err := socketio.NewServer(nil)
	if err != nil {
		log.Fatal(err)
	}
	server.On("connection", func(so socketio.Socket) {
		so.Join("updates")
		state.socket = &so

		so.Emit("update", "hello")
		so.On("chat message", func(msg string) {
			log.Println("emit:", so.Emit("chat message", msg))
			so.BroadcastTo("chat", "chat message", msg)
		})
		so.On("disconnection", func() {
		})
	})
	server.On("error", func(so socketio.Socket, err error) {
		log.Println("error:", err)
	})

	http.Handle("/socket.io/", server)

	for {
		msg := <-state.messages
		if state.socket != nil {
			so := *state.socket
			so.Emit("update", msg)
		}
	}
}

func main() {
	var router = mux.NewRouter()

	sess, err := mgo.Dial("localhost")
	if err != nil {
		panic(err)
	}
	defer sess.Close()
	state := ServerState{}
	db := sess.DB("doofer_irc")
	//db.C("channels").DropCollection()
	state.chans = db.C("channels")
	state.server = db.C("server")
	//db.C("chatMessages").DropCollection()
	state.chatMessages = db.C("chatMessages")

	// We tell the web client that we have updated information by communicating
	// with it over socket.io (http://socket.io/)
	state.socket = nil

	// We are told about database updates via web hook, which dumps the messages
	// in this queue for the socket.io handler function to pick up
	state.messages = make(chan string, 1000)

	// Paths on the web server are handled by functions. Wire them up.
	api := router.PathPrefix("/API").Subrouter()
	router.PathPrefix("/").Handler(http.FileServer(http.Dir("./static/")))

	api.HandleFunc("/chan", state.apiChanHandler)
	api.HandleFunc("/server", state.apiServerHandler)
	api.HandleFunc("/chan/msgs/{channel}", state.apiChanChatMessageHandler)
	api.HandleFunc("/priv/msgs/{channel}", state.apiPrivChatMessageHandler)
	api.HandleFunc("/say", state.apiSendMessageHandler)

	// TODO: Attach this to a services API rather than the public one!
	api.HandleFunc("/ping", state.apiPing)

	if true {
		bot := ircx.Classic(*server, *name)

		if err := bot.Connect(); err != nil {
			log.Panicln("Unable to dial IRC Server ", err)
		}

		RegisterHandlers(bot, &state)
		state.bot = bot
		go bot.CallbackLoop()
	}

	// Start the real time messaging handler
	go state.forwardUpdatesToSocketIO()
	http.Handle("/", router)
	log.Fatal(http.ListenAndServe(":6520", nil))

	log.Println("Exiting..")
}

func RegisterHandlers(bot *ircx.Bot, state *ServerState) {
	bot.AddCallback(irc.RPL_WELCOME, ircx.Callback{Handler: ircx.HandlerFunc(RegisterConnect)})
	bot.AddCallback(irc.PING, ircx.Callback{Handler: ircx.HandlerFunc(PingHandler)})

	serverMessages := []string{
		irc.RPL_MOTDSTART,
		irc.RPL_MOTD,
		irc.RPL_ENDOFMOTD,
		irc.RPL_CREATED,
		irc.RPL_BOUNCE,
		irc.RPL_YOURHOST,
		irc.RPL_MYINFO,
		irc.RPL_LUSERCLIENT,
		irc.RPL_LUSEROP,
		irc.RPL_LUSERUNKNOWN,
		irc.RPL_LUSERCHANNELS,
		irc.RPL_LUSERME,
		irc.MODE,
		irc.JOIN,
		irc.NOTICE,
		RPL_LOCALUSERS,
		RPL_GLOBALUSERS,
		RPL_STATSDLINE,
		RPL_TOPICWHOTIME,
	}

	for _, msg := range serverMessages {
		bot.AddCallback(msg, ircx.Callback{Handler: ircx.HandlerFunc(ServerChannelHandler)})
	}

	bot.AddCallback(irc.PRIVMSG, ircx.Callback{Handler: ircx.HandlerFunc(state.SomeChannelHandler)})
	bot.AddCallback(irc.RPL_NAMREPLY, ircx.Callback{Handler: ircx.HandlerFunc(state.NamesHandler)})
	bot.AddCallback(irc.RPL_ENDOFNAMES, ircx.Callback{Handler: ircx.HandlerFunc(state.NamesHandler)})
	bot.AddCallback(irc.TOPIC, ircx.Callback{Handler: ircx.HandlerFunc(TopicHandler)})
	bot.AddCallback(irc.RPL_TOPIC, ircx.Callback{Handler: ircx.HandlerFunc(TopicHandler)})
	bot.AddCallback(irc.RPL_NOTOPIC, ircx.Callback{Handler: ircx.HandlerFunc(TopicHandler)})
}

func RegisterConnect(s ircx.Sender, m *irc.Message) {
	s.Send(&irc.Message{Command: irc.JOIN, Params: []string{"#dooferbot"}})
	//s.Send(&irc.Message{Command: irc.JOIN, Params: []string{"#ubuntu"}})
	s.Send(&irc.Message{Command: irc.JOIN, Params: []string{"#juju-dev"}})
}

func PingHandler(s ircx.Sender, m *irc.Message) {
	s.Send(&irc.Message{
		Command:  irc.PONG,
		Params:   m.Params,
		Trailing: m.Trailing,
	})
}

func ServerChannelHandler(s ircx.Sender, m *irc.Message) {
	fmt.Printf("  Server: %s\n", m)
}

type ChatMessage struct {
	Channel   string   `json:"c" bson:"channel"`
	Message   string   `json:"m" bson:"message"`
	Timestamp int64    `json:"t" bson:"timestamp"`
	Name      string   `json:"n" bson:"name"`
	User      string   `json:"u" bson:"user"`
	Host      string   `json:"h" bson:"host"`
	Params    []string `json:"-" bson:"params"`
}

func (server *ServerState) say(msg ChatMessage) {
	// First ensure we think we have joined this channel. If not it is a
	// new direct chat and we still need to list nics in the chans collection
	n, err := server.chans.Find(bson.M{"channelName": msg.Channel}).Count()
	if err != nil || n == 0 {
		cd := ChannelData{
			ChannelName: msg.Channel,
			Names:       []string{msg.Name},
		}

		server.chans.Insert(cd)
	}

	// Assemble the message document and save it
	now := time.Now()
	msg.Timestamp = now.UnixNano() / (1000 * 1000)

	err = server.chatMessages.Insert(msg)
	if err != nil {
		fmt.Printf("Error recording message for channel %s\n", msg.Channel)
		here.Is(err)
	}
	str, err := json.Marshal(msg)
	if err != nil {
		fmt.Printf("Error encoding message: %v", err)
	}
	server.messages <- string(str)
	//server.ping()
}

func (server *ServerState) SomeChannelHandler(s ircx.Sender, m *irc.Message) {
	channel := m.Params[0]
	msg := ChatMessage{
		Channel: channel,
		Message: m.Trailing,
		Name:    m.Name,
		User:    m.User,
		Host:    m.Host,
		Params:  m.Params,
	}
	server.say(msg)
}

type ChannelData struct {
	ChannelName string   `bson:"channelName"`
	Names       []string `bson:"names"`
}

type MultiMessageStates uint

const (
	Idle MultiMessageStates = iota
	Rx
)

type NamesState struct {
	Names      map[string][]string
	NamesState MultiMessageStates
}

func (server *ServerState) NamesHandler(s ircx.Sender, m *irc.Message) {
	channel := m.Params[len(m.Params)-1]
	state := &server.namesState
	if m.Command == irc.RPL_ENDOFNAMES {
		cd := ChannelData{
			ChannelName: channel,
			Names:       state.Names[channel],
		}
		info, err := server.chans.Upsert(bson.M{"channelName": channel}, cd)
		if err != nil {
			fmt.Printf("Error updating names for channel %s\n", channel)
			here.Is(err)
			here.Is(info)
		}
		return
	}

	if state.NamesState == Idle {
		if state.Names == nil {
			state.Names = make(map[string][]string)
		}
		state.Names[channel] = []string{}
		state.NamesState = Rx
	}

	for _, name := range strings.Split(m.Trailing, " ") {
		state.Names[channel] = append(state.Names[channel], name)
	}
}

func TopicHandler(s ircx.Sender, m *irc.Message) {

	if m.Command == irc.RPL_TOPIC || m.Command == irc.TOPIC {
		fmt.Printf("Topic:%s\n", m.Trailing)
	} else {
		// no topic
	}
}

func (server *ServerState) SendMessage(channel, message string) {
	msg := ChatMessage{
		Channel: channel,
		Message: message,
		Name:    "dooferlad", // TODO: get this from server messages
		// TODO: get this from server messages User:      m.User,
		// TODO: get this from server messages Host:      m.Host,
	}

	server.say(msg)

	server.bot.Sender.Send(&irc.Message{
		Command:  irc.PRIVMSG,
		Params:   []string{channel},
		Trailing: message,
	})
}
